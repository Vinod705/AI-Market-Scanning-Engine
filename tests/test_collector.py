"""Tests for app.data.collector.MarketDataCollector, against a fake provider."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.models.collector_log import CollectorLog
from app.models.intraday_price import IntradayPrice
from app.models.symbol import Symbol
from app.providers.base_provider import (
    Candle,
    MarketDataProvider,
    ProviderError,
    ProviderSymbol,
    Quote,
)
from app.repositories.market_repository import SymbolRepository


class FakeProvider(MarketDataProvider):
    """Deterministic provider test double: no network, canned responses."""

    def __init__(self, *, fail_intraday_for: set[str] | None = None) -> None:
        self._connected = False
        self.fail_intraday_for = fail_intraday_for or set()

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def get_symbols(self) -> list[ProviderSymbol]:
        return [
            ProviderSymbol(
                symbol="TCS", exchange="N", instrument_token="11536", company_name="TCS Ltd"
            ),
            ProviderSymbol(
                symbol="INFY", exchange="N", instrument_token="1594", company_name="Infosys"
            ),
        ]

    async def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    async def get_intraday(self, symbol: str) -> list[Candle]:
        if symbol in self.fail_intraday_for:
            raise ProviderError(f"simulated failure for {symbol}")
        now = datetime(2026, 1, 5, 10, 0)
        return [
            Candle(timestamp=now, open=100, high=101, low=99, close=100.5, volume=1000),
            Candle(
                timestamp=now + timedelta(minutes=1),
                open=100.5,
                high=102,
                low=100,
                close=101,
                volume=1200,
            ),
        ]

    async def get_daily(self, symbol: str) -> list[Candle]:
        return [
            Candle(
                timestamp=datetime(2026, 1, 5), open=100, high=110, low=95, close=105, volume=50000
            )
        ]


async def _collector(
    session_factory: async_sessionmaker[AsyncSession], provider: MarketDataProvider
) -> MarketDataCollector:
    market_updater = MarketStatusUpdater(session_factory)
    return MarketDataCollector(provider, session_factory, market_updater)


async def test_collect_symbols_populates_symbol_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    collector = await _collector(session_factory, FakeProvider())

    result = await collector.collect_symbols()

    assert result.symbols_processed == 2
    assert result.success_count == 2
    assert result.failed_count == 0

    async with session_factory() as session:
        rows = (await session.execute(select(Symbol))).scalars().all()
        assert {row.symbol for row in rows} == {"TCS", "INFY"}


async def test_collect_intraday_stores_candles_for_active_symbols(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeProvider()
    collector = await _collector(session_factory, provider)
    await collector.collect_symbols()

    result = await collector.collect_intraday()

    assert result.symbols_processed == 2
    assert result.success_count == 2

    async with session_factory() as session:
        rows = (await session.execute(select(IntradayPrice))).scalars().all()
        assert len(rows) == 4  # 2 symbols * 2 candles


async def test_collect_intraday_isolates_per_symbol_failures(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeProvider(fail_intraday_for={"TCS"})
    collector = await _collector(session_factory, provider)
    await collector.collect_symbols()

    result = await collector.collect_intraday()

    assert result.success_count == 1
    assert result.failed_count == 1
    assert "TCS" in result.error_message

    async with session_factory() as session:
        rows = (await session.execute(select(IntradayPrice))).scalars().all()
        assert len(rows) == 2  # only INFY's candles landed


async def test_collector_writes_a_log_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    collector = await _collector(session_factory, FakeProvider())

    await collector.collect_symbols()

    async with session_factory() as session:
        logs = (await session.execute(select(CollectorLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].success_count == 2
        assert logs[0].finish_time is not None


async def test_collect_intraday_running_twice_does_not_duplicate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeProvider()
    collector = await _collector(session_factory, provider)
    await collector.collect_symbols()

    await collector.collect_intraday()
    await collector.collect_intraday()

    async with session_factory() as session:
        rows = (await session.execute(select(IntradayPrice))).scalars().all()
        assert len(rows) == 4  # re-running upserts, doesn't duplicate


async def test_symbol_repository_used_by_collector_marks_symbols_active(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    collector = await _collector(session_factory, FakeProvider())
    await collector.collect_symbols()

    async with session_factory() as session:
        active = await SymbolRepository(session).list_active()
        assert len(active) == 2
