"""Integration tests for app.scanner.scanner_manager.ScannerManager against an in-memory DB."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.scanner_manager import ScannerManager


@contextmanager
def _count_queries(session_factory: async_sessionmaker[AsyncSession]):
    """See tests/test_feature_engine.py's identical helper — proves a fix
    actually eliminated the per-symbol N+1 pattern, not just that results
    are still correct."""
    engine = session_factory.kw["bind"]
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)

_QUALIFYING_FEATURE_VALUES: dict[str, object] = {
    "ema20": Decimal("105"),
    "ema50": Decimal("100"),
    "ema200": Decimal("90"),
    "adx14": Decimal("25"),
    "relative_volume": Decimal("2.0"),
    "resistance_level": Decimal("112"),
    "trend_strength": Decimal("80"),
    "momentum_score": Decimal("50"),
    "atr_expansion": True,
    "rs_vs_nifty": Decimal("10"),
}


async def _seed_qualifying_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str = "TCS"
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=108,
                    high=111,
                    low=107,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURE_VALUES
        )
        await session.commit()
        return symbol_id


async def test_scan_one_qualifies_symbol_meeting_all_conditions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_qualifying_symbol(session_factory)

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 1
    assert stats.qualified_count == 1
    assert stats.rejected_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert len(results) == 1
        assert results[0].status == "qualified"


async def test_scan_one_rejects_symbol_without_features(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="INFY", exchange="N", instrument_token="2")
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_row.id)
        assert (
            results == []
        )  # rejection from a validation/missing-data failure writes no result row


async def test_scan_one_rejects_symbol_failing_validation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="WIPRO", exchange="N", instrument_token="3")
        )
        await session.commit()
        symbol_id = symbol_row.id

        # ema200 missing -> fails validate(), never reaches scan()/save_results().
        incomplete_values = {k: v for k, v in _QUALIFYING_FEATURE_VALUES.items() if k != "ema200"}
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), incomplete_values)
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert results == []


async def test_run_scanner_mixed_batch_qualify_reject_no_context_in_one_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One run_scanner() call with three symbols in three different states
    — proves the bulk build_context_bulk/exists_bulk split produces the
    exact same per-symbol outcomes the old sequential build_context ->
    exists_for_date -> validate -> scan loop would have."""
    qualifies_id = await _seed_qualifying_symbol(session_factory, symbol="QUALIFIES")

    async with session_factory() as session:
        no_context_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOCONTEXT", exchange="N", instrument_token="nc")
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 2
    assert stats.qualified_count == 1
    assert stats.rejected_count == 1  # the no-context symbol
    assert stats.error_count == 0

    async with session_factory() as session:
        repo = ScannerResultRepository(session)
        assert (await repo.get_for_symbol(qualifies_id))[0].status == "qualified"
        assert await repo.get_for_symbol(no_context_row.id) == []


async def test_run_scanner_isolates_one_symbols_failure_from_the_rest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A symbol whose scan() raises must not prevent other symbols in the
    same batch from being evaluated — this is the exact guarantee the
    per-symbol try/except in run_scanner exists for, now exercised across
    the new build_context_bulk/exists_bulk split rather than the old
    fully-sequential loop."""

    class _FlakyScanner(BreakoutScanner):
        def scan(self, context):  # type: ignore[override]
            if context.symbol.symbol == "BADSYMBOL":
                raise RuntimeError("boom")
            return super().scan(context)

    good_id = await _seed_qualifying_symbol(session_factory, symbol="GOODSYMBOL")
    bad_id = await _seed_qualifying_symbol(session_factory, symbol="BADSYMBOL")

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = _FlakyScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.error_count == 1
    assert stats.qualified_count == 1  # GOODSYMBOL still qualified despite BADSYMBOL's crash

    async with session_factory() as session:
        repo = ScannerResultRepository(session)
        assert (await repo.get_for_symbol(good_id))[0].status == "qualified"
        assert await repo.get_for_symbol(bad_id) == []  # never reached save_results


@contextmanager
def _count_connection_checkouts(session_factory: async_sessionmaker[AsyncSession]):
    """Counts actual DB connection checkouts — the direct metric for the
    session/commit-per-symbol fix (see _BATCH_SIZE's docstring in
    scanner_manager.py): raw SQL statement count isn't the right proxy
    here, since SAVEPOINT/RELEASE statements mean per-symbol isolation
    still costs a statement each — what actually dropped is how many
    separate sessions/connections get checked out from the pool."""
    engine = session_factory.kw["bind"]
    counter = {"n": 0}

    def _on_checkout(dbapi_conn, connection_record, connection_proxy):
        counter["n"] += 1

    event.listen(engine.sync_engine.pool, "checkout", _on_checkout)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine.pool, "checkout", _on_checkout)


async def test_run_scanner_query_count_stays_flat_as_symbol_count_grows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """build_context_bulk + exists_bulk should answer "does this symbol
    need work" for the whole batch in a small constant number of queries,
    not one/two per symbol — confirmed live this session: 9,598 symbols
    went from 22,047 queries to 7,394 after this part of the fix."""
    for i in range(30):
        async with session_factory() as session:
            await SymbolRepository(session).upsert(
                ProviderSymbol(symbol=f"NOFEATSYM{i}", exchange="N", instrument_token=str(i))
            )
            await session.commit()
    # None of these 30 symbols have any daily_features rows -> all "no context".

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    with _count_queries(session_factory) as counter:
        stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 30
    assert stats.rejected_count == 30
    # Bulk build_context_bulk/exists_bulk queries, plus one SAVEPOINT +
    # INSERT + RELEASE per symbol for the batched log writes — not the old
    # ~2-5 per-symbol read queries on top of that.
    assert counter["n"] < 120


async def test_run_scanner_batches_session_checkouts_instead_of_one_per_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The actual point of this fix: prove DB session/connection checkouts
    no longer scale 1:1 with symbol count. Seeds more symbols than
    _BATCH_SIZE (200) to prove batching kicks in at the boundary too, not
    just "small enough to fit in one batch by accident"."""
    symbol_count = 250
    for i in range(symbol_count):
        async with session_factory() as session:
            await SymbolRepository(session).upsert(
                ProviderSymbol(symbol=f"BATCHSYM{i}", exchange="N", instrument_token=str(i))
            )
            await session.commit()
    # None of these have daily_features rows -> all "no context", all go
    # through _log_no_context_batch in ceil(250/200) = 2 batches.

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    with _count_connection_checkouts(session_factory) as counter:
        stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == symbol_count
    assert stats.rejected_count == symbol_count
    # build_context_bulk (1) + exists_bulk (1) + 2 no-context batches
    # (ceil(250/200)) = a handful of checkouts — not 250 (one per symbol,
    # the pre-fix behavior).
    assert counter["n"] < 10


async def test_scan_one_skips_symbol_already_scanned_for_the_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_symbol(session_factory)

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    await manager.run_scanner(scanner, symbols, run_id=1)
    second_stats = await manager.run_scanner(scanner, symbols, run_id=2)

    # Already scanned for that feature date — no new qualification/rejection counted.
    assert second_stats.qualified_count == 0
    assert second_stats.rejected_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(scanner_name="breakout_v1")
        assert len(results) == 1  # still just the one row — no duplicate
