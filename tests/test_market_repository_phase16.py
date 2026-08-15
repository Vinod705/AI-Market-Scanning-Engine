"""Tests for the Phase 16 additions to app.repositories.market_repository —
PriceRepository.get_intraday_at_or_after/get_daily_at_or_after and the
CollectorLogRepository/MarketDataFeedLogRepository list_recent methods."""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import (
    CollectorLogRepository,
    MarketDataFeedLogRepository,
    PriceRepository,
    SymbolRepository,
)


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        return row.id


async def test_get_intraday_at_or_after_returns_earliest_bar_at_or_after_moment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "TESTSYM")
    async with session_factory() as session:
        repo = PriceRepository(session)
        await repo.upsert_intraday_many(
            symbol_id,
            [
                Candle(timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=UTC), open=100, high=101, low=99, close=100.5, volume=1000),
                Candle(timestamp=datetime(2026, 1, 5, 10, 15, tzinfo=UTC), open=100.5, high=102, low=100, close=101.5, volume=1200),
                Candle(timestamp=datetime(2026, 1, 5, 10, 30, tzinfo=UTC), open=101.5, high=103, low=101, close=102.5, volume=900),
            ],
        )
        await session.commit()

        bar = await repo.get_intraday_at_or_after(symbol_id, datetime(2026, 1, 5, 10, 10, tzinfo=UTC))
    assert bar is not None
    assert bar.datetime.replace(tzinfo=UTC) == datetime(2026, 1, 5, 10, 15, tzinfo=UTC)
    assert float(bar.close) == 101.5


async def test_get_intraday_at_or_after_returns_none_when_nothing_arrived_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "NOBARS")
    async with session_factory() as session:
        repo = PriceRepository(session)
        bar = await repo.get_intraday_at_or_after(symbol_id, datetime(2026, 1, 5, 10, 0, tzinfo=UTC))
    assert bar is None


async def test_get_daily_at_or_after_returns_earliest_bar_on_or_after_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "DAILYSYM")
    async with session_factory() as session:
        repo = PriceRepository(session)
        await repo.upsert_daily_many(
            symbol_id,
            [
                Candle(timestamp=datetime(2026, 1, 5), open=100, high=101, low=99, close=100.5, volume=100_000),
                Candle(timestamp=datetime(2026, 1, 7), open=100.5, high=103, low=100, close=102.5, volume=110_000),
            ],
        )
        await session.commit()

        bar = await repo.get_daily_at_or_after(symbol_id, date(2026, 1, 6))
    assert bar is not None
    assert bar.date == date(2026, 1, 7)
    assert float(bar.close) == 102.5


async def test_collector_log_repository_list_recent_orders_newest_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = CollectorLogRepository(session)
        older = await repo.start(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        await repo.finish(older, finish_time=datetime(2026, 1, 5, 9, 1, tzinfo=UTC), symbols_processed=10, success_count=10, failed_count=0, error_message=None)
        newer = await repo.start(datetime(2026, 1, 5, 10, 0, tzinfo=UTC))
        await repo.finish(newer, finish_time=datetime(2026, 1, 5, 10, 1, tzinfo=UTC), symbols_processed=5, success_count=3, failed_count=2, error_message="two symbols failed")
        await session.commit()

        rows = await repo.list_recent(limit=10)

    assert [r.symbols_processed for r in rows] == [5, 10]
    assert rows[0].failed_count == 2
    assert rows[0].error_message == "two symbols failed"


async def test_market_data_feed_log_repository_list_recent_orders_newest_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = MarketDataFeedLogRepository(session)
        older = await repo.open(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        await repo.close(older, disconnected_at=datetime(2026, 1, 5, 9, 30, tzinfo=UTC), messages_received=100, ticks_processed=95, duplicates_dropped=5, candles_flushed=10, disconnect_reason="normal shutdown")
        newer = await repo.open(datetime(2026, 1, 5, 10, 0, tzinfo=UTC))
        await repo.close(newer, disconnected_at=datetime(2026, 1, 5, 10, 5, tzinfo=UTC), messages_received=20, ticks_processed=18, duplicates_dropped=2, candles_flushed=1, disconnect_reason="connection reset")
        await session.commit()

        rows = await repo.list_recent(limit=10)

    assert [r.disconnect_reason for r in rows] == ["connection reset", "normal shutdown"]
