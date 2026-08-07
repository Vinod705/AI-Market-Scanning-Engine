"""Integration tests for app.features.engine.FeatureEngine against an in-memory DB."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.features.engine import FeatureEngine
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository


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
