"""Tests for app.scanner.orb_scanner.OrbScanner."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.models.daily_feature import DailyFeature
from app.models.session_feature import SessionFeature
from app.models.symbol import Symbol
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import SessionFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.orb_scanner import OrbScanContext, OrbScanner
from app.scanner.scanner_manager import ScannerManager
from app.scanner.scanner_registry import ScannerRegistry

_SESSION_DATE = date(2026, 1, 5)


@contextmanager
def _count_queries(session_factory: async_sessionmaker[AsyncSession]):
    """See tests/test_scanner_manager.py's identical helper."""
    engine = session_factory.kw["bind"]
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)


def _context(
    price: Decimal | None = Decimal("110"),
    price_date: date | None = _SESSION_DATE,
    session_date: date = _SESSION_DATE,
    opening_range_high: Decimal | None = Decimal("108"),
    opening_range_low: Decimal | None = Decimal("102"),
    daily_feature: DailyFeature | None = None,
) -> OrbScanContext:
    symbol = Symbol(id=1, symbol="TCS", exchange="N", instrument_token="1")
    session_feature = SessionFeature(
        symbol_id=1,
        date=session_date,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
    )
    return OrbScanContext(
        symbol=symbol,
        session_feature=session_feature,
        price=price,
        price_date=price_date,
        daily_feature=daily_feature,
    )


@pytest.fixture
def scanner() -> OrbScanner:
    return OrbScanner(Settings())


# --- validate() ----------------------------------------------------------


def test_validate_passes_with_price_and_opening_range_present(scanner: OrbScanner) -> None:
    result = scanner.validate(_context())
    assert result.valid is True


def test_validate_rejects_missing_price(scanner: OrbScanner) -> None:
    result = scanner.validate(_context(price=None))
    assert result.valid is False
    assert result.reason is not None
    assert "price" in result.reason


def test_validate_rejects_missing_opening_range_high(scanner: OrbScanner) -> None:
    result = scanner.validate(_context(opening_range_high=None))
    assert result.valid is False
    assert result.reason is not None
    assert "opening range" in result.reason


def test_validate_rejects_missing_opening_range_low(scanner: OrbScanner) -> None:
    result = scanner.validate(_context(opening_range_low=None))
    assert result.valid is False
    assert result.reason is not None
    assert "opening range" in result.reason


def test_validate_rejects_price_from_a_different_trading_day(scanner: OrbScanner) -> None:
    result = scanner.validate(_context(price_date=date(2026, 1, 4), session_date=_SESSION_DATE))
    assert result.valid is False
    assert result.reason is not None
    assert "same trading day" in result.reason


# --- scan() ----------------------------------------------------------------


def test_scan_price_exactly_at_opening_range_high_does_not_qualify_bullish(
    scanner: OrbScanner,
) -> None:
    outcome = scanner.scan(_context(price=Decimal("108")))
    assert outcome.qualified is False


def test_scan_price_above_opening_range_high_qualifies_bullish(scanner: OrbScanner) -> None:
    outcome = scanner.scan(_context(price=Decimal("108.01")))
    assert outcome.qualified is True
    assert "bullish" in outcome.reason


def test_scan_price_exactly_at_opening_range_low_does_not_qualify_bearish(
    scanner: OrbScanner,
) -> None:
    outcome = scanner.scan(_context(price=Decimal("102")))
    assert outcome.qualified is False


def test_scan_price_below_opening_range_low_qualifies_bearish(scanner: OrbScanner) -> None:
    outcome = scanner.scan(_context(price=Decimal("101.99")))
    assert outcome.qualified is True
    assert "bearish" in outcome.reason


def test_scan_price_within_opening_range_does_not_qualify(scanner: OrbScanner) -> None:
    outcome = scanner.scan(_context(price=Decimal("105")))
    assert outcome.qualified is False


# --- score() -----------------------------------------------------------


def test_score_is_always_within_bounds_with_no_daily_feature(scanner: OrbScanner) -> None:
    score = scanner.score(_context(daily_feature=None))
    assert 0.0 <= score <= 100.0


def test_score_matches_expected_weighted_composite(scanner: OrbScanner) -> None:
    # Same formula/fields as BreakoutScanner/VcpScanner/MomentumScanner's
    # score() — see module docstring — locks in the reuse is byte-for-byte.
    features = DailyFeature(
        symbol_id=1,
        date=_SESSION_DATE,
        trend_strength=Decimal("80"),
        momentum_score=Decimal("50"),
        relative_volume=Decimal("2.0"),
        atr_expansion=False,
        atr_contraction=True,
        rs_vs_nifty=Decimal("10"),
        resistance_level=Decimal("112"),
    )
    score = scanner.score(_context(price=Decimal("110"), daily_feature=features))
    assert score == pytest.approx(70.07, abs=0.01)


def test_scan_qualification_independent_of_score(scanner: OrbScanner) -> None:
    """A qualifying breakout can still score low — score and scan are
    independent, matching BaseScanner's documented contract."""
    context = _context(price=Decimal("109"), daily_feature=None)
    outcome = scanner.scan(context)
    score = scanner.score(context)
    assert outcome.qualified is True
    assert score < 50.0


# --- registry --------------------------------------------------------------


def test_orb_registers_in_scanner_registry_by_name() -> None:
    registry = ScannerRegistry()
    registry.register(OrbScanner(Settings()))
    found = registry.get("orb_v1")
    assert found is not None
    assert found.name == "orb_v1"
    assert registry.get_all() == [found]


# --- integration via ScannerManager/DB --------------------------------------


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
        )
        await session.commit()
        return symbol_row.id


async def test_run_scanner_persists_qualified_bullish_orb_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end through the real ScannerManager/DB path — proves ORB
    results land in the same scanner_results mechanism every other scanner
    uses, not a special-cased new one."""
    symbol_id = await _seed_symbol(session_factory, "ORBBULL")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=105,
                    high=112,
                    low=104,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await SessionFeatureRepository(session).upsert(
            symbol_id,
            _SESSION_DATE,
            {"opening_range_high": Decimal("108"), "opening_range_low": Decimal("102")},
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 1
    assert stats.qualified_count == 1
    assert stats.rejected_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert len(results) == 1
        assert results[0].scanner_name == "orb_v1"
        assert results[0].status == "qualified"


async def test_run_scanner_persists_qualified_bearish_orb_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "ORBBEAR")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=104,
                    high=105,
                    low=98,
                    close=99,
                    volume=100_000,
                )
            ],
        )
        await SessionFeatureRepository(session).upsert(
            symbol_id,
            _SESSION_DATE,
            {"opening_range_high": Decimal("108"), "opening_range_low": Decimal("102")},
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.qualified_count == 1
    assert stats.rejected_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert len(results) == 1
        assert results[0].status == "qualified"


async def test_run_scanner_handles_symbol_with_no_session_feature(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Insufficient/missing data: a symbol with zero session_features rows
    goes through the existing "no context" rejection path, not an error."""
    symbol_id = await _seed_symbol(session_factory, "ORBNOSESS")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=105,
                    high=112,
                    low=104,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert results == []


async def test_run_scanner_rejects_when_opening_range_not_yet_available(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SessionFeature row exists but opening_range_high/low are still None
    (the state before the opening-range window's bars exist) — timing rule:
    a breakout can't be valid before the opening range has completed."""
    symbol_id = await _seed_symbol(session_factory, "ORBNORANGE")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=105,
                    high=112,
                    low=104,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await SessionFeatureRepository(session).upsert(symbol_id, _SESSION_DATE, {})
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert results == []  # validation failure writes no scanner_results row


async def test_run_scanner_rejects_when_price_and_session_are_different_trading_days(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same-day requirement: a stale daily close from a prior day must not
    be compared against a different day's opening range."""
    symbol_id = await _seed_symbol(session_factory, "ORBSTALE")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 4),  # a day before the session below
                    open=105,
                    high=112,
                    low=104,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await SessionFeatureRepository(session).upsert(
            symbol_id,
            _SESSION_DATE,  # 2026-01-05
            {"opening_range_high": Decimal("108"), "opening_range_low": Decimal("102")},
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert results == []


async def test_run_scanner_skips_symbol_already_scanned_for_the_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "ORBDEDUPE")
    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=105,
                    high=112,
                    low=104,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await SessionFeatureRepository(session).upsert(
            symbol_id,
            _SESSION_DATE,
            {"opening_range_high": Decimal("108"), "opening_range_low": Decimal("102")},
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = OrbScanner(Settings())
    await manager.run_scanner(scanner, symbols, run_id=1)
    second_stats = await manager.run_scanner(scanner, symbols, run_id=2)

    assert second_stats.qualified_count == 0
    assert second_stats.rejected_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(scanner_name="orb_v1")
        assert len(results) == 1  # still just the one row — no duplicate


async def test_build_context_bulk_query_count_stays_flat_as_symbol_count_grows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """OrbScanner.build_context_bulk must answer "what's each symbol's
    latest SessionFeature/DailyPrice/DailyFeature" for the whole batch in a
    small constant number of queries, not one (or more) per symbol — same
    bulk-read contract as BreakoutScanner/VcpScanner/MomentumScanner."""
    symbol_count = 40
    for i in range(symbol_count):
        async with session_factory() as session:
            symbol_row = await SymbolRepository(session).upsert(
                ProviderSymbol(symbol=f"ORBQSYM{i}", exchange="N", instrument_token=str(i))
            )
            await session.commit()
            await PriceRepository(session).upsert_daily_many(
                symbol_row.id,
                [
                    Candle(
                        timestamp=datetime(2026, 1, 5),
                        open=105,
                        high=112,
                        low=104,
                        close=110,
                        volume=100_000,
                    )
                ],
            )
            await SessionFeatureRepository(session).upsert(
                symbol_row.id,
                _SESSION_DATE,
                {"opening_range_high": Decimal("108"), "opening_range_low": Decimal("102")},
            )
            await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    scanner = OrbScanner(Settings())
    with _count_queries(session_factory) as counter:
        async with session_factory() as session:
            contexts = await scanner.build_context_bulk(session, symbols)

    assert len(contexts) == symbol_count
    # Three bulk queries total (SessionFeature, DailyPrice, DailyFeature) —
    # not one (or more) per symbol.
    assert counter["n"] <= 5
