"""Integration tests for app.features.engine.FeatureEngine against an in-memory DB."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.features.engine as engine_module
from app.config.settings import Settings
from app.features.engine import FeatureEngine
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository


@contextmanager
def _count_queries(session_factory: async_sessionmaker[AsyncSession]):
    """Counts SQL statements executed against `session_factory`'s engine —
    used to prove a fix actually eliminated the per-symbol N+1 pattern
    (see test_bulk_prefilters_keep_query_count_flat_as_symbol_count_grows
    below), not just that it "still returns the right answer"."""
    engine = session_factory.kw["bind"]
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)


def _settings() -> Settings:
    return Settings(feature_daily_lookback_bars=300)


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str = "TCS"
) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token="1")
        )
        await session.commit()
        return row.id


async def _seed_daily_bars(
    session_factory: async_sessionmaker[AsyncSession], symbol_id: int, n: int, start: datetime
) -> None:
    async with session_factory() as session:
        repo = PriceRepository(session)
        candles = [
            Candle(
                timestamp=start + timedelta(days=i),
                open=100 + i,
                high=101 + i,
                low=99 + i,
                close=100.5 + i,
                volume=1000 + i * 10,
            )
            for i in range(n)
        ]
        await repo.upsert_daily_many(symbol_id, candles)
        await session.commit()


async def test_run_daily_backfills_all_history_on_first_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    await _seed_daily_bars(session_factory, symbol_id, n=30, start=datetime(2026, 1, 1))

    engine = FeatureEngine(session_factory, _settings())
    result = await engine.run_daily()

    assert result.symbols_processed == 1
    assert result.symbols_updated == 1
    assert result.rows_written == 30

    async with session_factory() as session:
        history = await DailyFeatureRepository(session).get_history(symbol_id, limit=100)
        assert len(history) == 30
        assert history[-1].sma20 is not None  # last bar has enough warmup for a 20-period SMA


async def test_run_daily_is_a_noop_without_new_candles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    await _seed_daily_bars(session_factory, symbol_id, n=25, start=datetime(2026, 1, 1))

    engine = FeatureEngine(session_factory, _settings())
    await engine.run_daily()
    second_result = await engine.run_daily()

    assert second_result.rows_written == 0
    assert second_result.symbols_updated == 0


async def test_run_daily_writes_only_the_new_row_after_one_more_candle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    await _seed_daily_bars(session_factory, symbol_id, n=25, start=datetime(2026, 1, 1))

    engine = FeatureEngine(session_factory, _settings())
    await engine.run_daily()

    await _seed_daily_bars(session_factory, symbol_id, n=1, start=datetime(2026, 1, 26))
    second_result = await engine.run_daily()

    assert second_result.rows_written == 1

    async with session_factory() as session:
        history = await DailyFeatureRepository(session).get_history(symbol_id, limit=100)
        assert len(history) == 26


async def test_run_daily_skips_symbol_with_no_price_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory)  # symbol exists, but no daily_prices rows

    engine = FeatureEngine(session_factory, _settings())
    result = await engine.run_daily()

    assert result.symbols_processed == 1
    assert result.symbols_updated == 0
    assert result.rows_written == 0


async def test_run_daily_isolates_one_symbols_calculation_failure(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One symbol's feature calculation raising must not stop the run —
    other symbols still get processed, and the failure is reported, not
    swallowed silently."""
    good_id = await _seed_symbol(session_factory, symbol="GOOD")
    await _seed_daily_bars(session_factory, good_id, n=25, start=datetime(2026, 1, 1))
    bad_id = await _seed_symbol(session_factory, symbol="BAD")
    async with session_factory() as session:
        # A distinctly-priced series (starts at 900.5, vs GOOD's 100.5) so
        # the fake calculator below can target only this one symbol.
        candles = [
            Candle(
                timestamp=datetime(2026, 1, 1) + timedelta(days=i),
                open=900 + i, high=901 + i, low=899 + i, close=900.5 + i, volume=1000 + i * 10,
            )
            for i in range(25)
        ]
        await PriceRepository(session).upsert_daily_many(bad_id, candles)
        await session.commit()

    real_calculate = engine_module.DailyFeatureCalculator.calculate

    def _flaky_calculate(df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> pd.DataFrame:
        if len(df) and float(df["close"].iloc[0]) == 900.5:
            raise ValueError("simulated calculation failure")
        return real_calculate(df, benchmark_df)

    monkeypatch.setattr(engine_module.DailyFeatureCalculator, "calculate", _flaky_calculate)

    engine = FeatureEngine(session_factory, _settings())
    result = await engine.run_daily()

    assert result.symbols_updated == 1  # GOOD still succeeded
    assert result.rows_written == 25
    assert len(result.errors) == 1
    assert "BAD" in result.errors[0]
    assert "simulated calculation failure" in result.errors[0]

    async with session_factory() as session:
        good_history = await DailyFeatureRepository(session).get_history(good_id, limit=100)
        bad_history = await DailyFeatureRepository(session).get_history(bad_id, limit=100)
    assert len(good_history) == 25
    assert bad_history == []  # nothing partially written for the failed symbol


async def test_daily_feature_upsert_is_idempotent_for_same_symbol_and_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Calling upsert() twice for the same (symbol_id, date) must update
    the one row in place, never create a duplicate — the project's
    existing uniqueness strategy (unique index on symbol_id+date), not a
    new one invented for this test."""
    symbol_id = await _seed_symbol(session_factory)
    target_date = date(2026, 1, 5)

    async with session_factory() as session:
        repo = DailyFeatureRepository(session)
        await repo.upsert(symbol_id, target_date, {"trend_strength": 50})
        await session.commit()

    async with session_factory() as session:
        repo = DailyFeatureRepository(session)
        await repo.upsert(symbol_id, target_date, {"trend_strength": 75})
        await session.commit()

    async with session_factory() as session:
        history = await DailyFeatureRepository(session).get_history(symbol_id, limit=100)

    assert len(history) == 1  # still one row, not two
    assert float(history[0].trend_strength) == 75  # updated in place


async def test_run_daily_bulk_prefilter_correct_across_mixed_symbol_states(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One run_daily() call with symbols in three different states — proves
    the bulk pre-filter (computed once for the whole batch) selects the
    exact same subset the old per-symbol early-exit logic would have."""
    no_data_id = await _seed_symbol(session_factory, symbol="NODATA")

    up_to_date_id = await _seed_symbol(session_factory, symbol="UPTODATE")
    await _seed_daily_bars(session_factory, up_to_date_id, n=25, start=datetime(2026, 1, 1))

    needs_update_id = await _seed_symbol(session_factory, symbol="NEEDSUPDATE")
    await _seed_daily_bars(session_factory, needs_update_id, n=25, start=datetime(2026, 1, 1))

    engine = FeatureEngine(session_factory, _settings())
    await engine.run_daily()  # first pass computes features for both seeded-with-price symbols

    # One more bar only for needs_update_id, after the first pass.
    await _seed_daily_bars(session_factory, needs_update_id, n=1, start=datetime(2026, 1, 26))

    result = await engine.run_daily()

    assert result.symbols_processed == 3
    assert result.symbols_updated == 1  # only needs_update_id had new work
    assert result.rows_written == 1

    async with session_factory() as session:
        repo = DailyFeatureRepository(session)
        no_data_history = await repo.get_history(no_data_id, limit=100)
        up_to_date_history = await repo.get_history(up_to_date_id, limit=100)
        needs_update_history = await repo.get_history(needs_update_id, limit=100)
    assert no_data_history == []
    assert len(up_to_date_history) == 25
    assert len(needs_update_history) == 26


async def test_bulk_prefilter_keeps_query_count_flat_as_symbol_count_grows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The actual point of the fix: run_daily() used to cost ~1-2 DB round
    trips per symbol just to determine "nothing to do here" — confirmed
    live this session (~9,598 symbols, ~12,000+ queries for a single
    pipeline cycle). The bulk pre-filter should answer that in a small
    constant number of queries regardless of symbol count."""
    for i in range(30):
        await _seed_symbol(session_factory, symbol=f"SYM{i}")
    # None of these 30 symbols have any daily_prices rows at all.

    engine = FeatureEngine(session_factory, _settings())
    with _count_queries(session_factory) as counter:
        result = await engine.run_daily()

    assert result.symbols_processed == 30
    assert result.symbols_updated == 0
    assert counter["n"] < 10  # a handful of bulk queries, not ~30+ per-symbol ones


async def test_run_session_writes_day_high_low(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    today = datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)

    async with session_factory() as session:
        repo = PriceRepository(session)
        candles = [
            Candle(
                timestamp=today + timedelta(minutes=i),
                open=100,
                high=101 + (i % 5),
                low=99 - (i % 3),
                close=100 + (i % 2),
                volume=500,
            )
            for i in range(20)
        ]
        await repo.upsert_intraday_many(symbol_id, candles)
        await session.commit()

    engine = FeatureEngine(session_factory, _settings())
    result = await engine.run_session()

    assert result.rows_written == 1

    async with session_factory() as session:
        latest = await SessionFeatureRepository(session).get_latest(symbol_id)
        assert latest is not None
        assert latest.day_high is not None
        assert latest.day_low is not None


async def test_run_session_bulk_prefilter_skips_symbols_with_no_data_today(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mixed batch: one symbol has today's intraday bars, one doesn't —
    proves the bulk "who has data today" pre-filter matches exactly what
    the old per-symbol get_intraday_for_date() early-exit would have."""
    has_data_id = await _seed_symbol(session_factory, symbol="HASDATA")
    no_data_id = await _seed_symbol(session_factory, symbol="NODATA")
    today = datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)

    async with session_factory() as session:
        await PriceRepository(session).upsert_intraday_many(
            has_data_id,
            [Candle(timestamp=today, open=100, high=101, low=99, close=100, volume=500)],
        )
        await session.commit()

    engine = FeatureEngine(session_factory, _settings())
    result = await engine.run_session()

    assert result.symbols_processed == 2  # both counted, only one had work
    assert result.symbols_updated == 1
    assert result.rows_written == 1

    async with session_factory() as session:
        repo = SessionFeatureRepository(session)
        assert await repo.get_latest(has_data_id) is not None
        assert await repo.get_latest(no_data_id) is None


async def test_run_session_query_count_stays_flat_as_symbol_count_grows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(30):
        await _seed_symbol(session_factory, symbol=f"SESSSYM{i}")
    # None of these 30 symbols have any intraday_prices rows today.

    engine = FeatureEngine(session_factory, _settings())
    with _count_queries(session_factory) as counter:
        result = await engine.run_session()

    assert result.symbols_processed == 30
    assert result.symbols_updated == 0
    assert counter["n"] < 10
