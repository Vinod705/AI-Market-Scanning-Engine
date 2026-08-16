"""Tests for app.data.collector.MarketDataCollector, against a fake provider."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
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

    _DEFAULT_SYMBOLS = [
        ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536", company_name="TCS Ltd"),
        ProviderSymbol(symbol="INFY", exchange="N", instrument_token="1594", company_name="Infosys"),
    ]

    def __init__(
        self,
        *,
        fail_intraday_for: set[str] | None = None,
        symbols: list[ProviderSymbol] | None = None,
    ) -> None:
        self._connected = False
        self.fail_intraday_for = fail_intraday_for or set()
        self._symbols = symbols if symbols is not None else list(self._DEFAULT_SYMBOLS)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def get_symbols(self) -> list[ProviderSymbol]:
        return self._symbols

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
    session_factory: async_sessionmaker[AsyncSession],
    provider: MarketDataProvider,
    settings: Settings | None = None,
) -> MarketDataCollector:
    market_updater = MarketStatusUpdater(session_factory)
    return MarketDataCollector(provider, session_factory, market_updater, settings)


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


# --- Universe reconciliation (is_active deactivation) ---


def _symbol(name: str, token: str) -> ProviderSymbol:
    return ProviderSymbol(symbol=name, exchange="N", instrument_token=token)


async def test_reconciliation_deactivates_a_symbol_missing_from_a_full_refetch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The real-world case this guards: a symbol genuinely delisted /
    removed from the broker's own universe."""
    first_provider = FakeProvider(
        symbols=[_symbol("TCS", "1"), _symbol("INFY", "2"), _symbol("WIPRO", "3")]
    )
    collector = await _collector(session_factory, first_provider)
    await collector.collect_symbols()

    second_provider = FakeProvider(symbols=[_symbol("TCS", "1"), _symbol("INFY", "2")])
    collector = await _collector(session_factory, second_provider)
    result = await collector.collect_symbols()

    assert result.deactivated_symbols == ["WIPRO"]
    assert result.reconciliation_note is None

    async with session_factory() as session:
        active = {s.symbol for s in await SymbolRepository(session).list_active()}
    assert active == {"TCS", "INFY"}


async def test_reconciliation_adds_a_new_symbol_without_touching_others(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    collector = await _collector(session_factory, FakeProvider(symbols=[_symbol("TCS", "1")]))
    await collector.collect_symbols()

    collector = await _collector(
        session_factory, FakeProvider(symbols=[_symbol("TCS", "1"), _symbol("NEWIPO", "9")])
    )
    result = await collector.collect_symbols()

    assert result.deactivated_symbols == []
    async with session_factory() as session:
        active = {s.symbol for s in await SymbolRepository(session).list_active()}
    assert active == {"TCS", "NEWIPO"}


async def test_reconciliation_leaves_unchanged_universe_untouched(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_symbols = [_symbol("TCS", "1"), _symbol("INFY", "2")]
    collector = await _collector(session_factory, FakeProvider(symbols=provider_symbols))
    await collector.collect_symbols()

    result = await collector.collect_symbols()  # identical fetch again

    assert result.deactivated_symbols == []
    async with session_factory() as session:
        active = {s.symbol for s in await SymbolRepository(session).list_active()}
    assert active == {"TCS", "INFY"}


async def test_reconciliation_skips_deactivation_on_a_suspiciously_partial_fetch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A transient/partial API response that returns far fewer symbols
    than were already active must NOT be treated as a mass-delisting —
    the safety-fraction guard (default 50%) refuses to deactivate."""
    full_universe = [_symbol(f"SYM{i}", str(i)) for i in range(10)]
    collector = await _collector(session_factory, FakeProvider(symbols=full_universe))
    await collector.collect_symbols()

    partial_provider = FakeProvider(symbols=full_universe[:2])  # only 2 of 10 — 20%
    collector = await _collector(session_factory, partial_provider)
    result = await collector.collect_symbols()

    assert result.deactivated_symbols == []
    assert result.reconciliation_note is not None
    assert "skipped" in result.reconciliation_note

    async with session_factory() as session:
        active = await SymbolRepository(session).list_active()
    assert len(active) == 10  # nothing deactivated despite the tiny fetch


async def test_reconciliation_respects_a_configured_min_fraction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    full_universe = [_symbol(f"SYM{i}", str(i)) for i in range(10)]
    collector = await _collector(session_factory, FakeProvider(symbols=full_universe))
    await collector.collect_symbols()

    # 6 of 10 (60%) — below a strict 90% requirement, above the 50% default.
    strict_settings = Settings(universe_reconciliation_min_fraction=0.9)
    partial_provider = FakeProvider(symbols=full_universe[:6])
    collector = await _collector(session_factory, partial_provider, strict_settings)
    result = await collector.collect_symbols()

    assert result.deactivated_symbols == []
    assert result.reconciliation_note is not None


async def test_no_deactivation_when_the_fetch_itself_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A hard provider failure (network error, auth failure, etc.) must
    never be treated as 'the universe shrank' — collect_symbols() should
    abort before reconciliation runs at all."""

    class _RaisingProvider(FakeProvider):
        async def get_symbols(self) -> list[ProviderSymbol]:
            raise ProviderError("simulated network failure")

    collector = await _collector(session_factory, FakeProvider(symbols=[_symbol("TCS", "1")]))
    await collector.collect_symbols()

    collector = await _collector(session_factory, _RaisingProvider())
    result = await collector.collect_symbols()

    assert result.deactivated_symbols == []
    assert result.errors  # the failure is visible, not swallowed
    async with session_factory() as session:
        active = {s.symbol for s in await SymbolRepository(session).list_active()}
    assert active == {"TCS"}  # untouched


async def test_reconciliation_handles_duplicate_symbols_in_one_fetch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A provider response containing the same symbol twice (a genuine
    upstream data-quality issue) must not crash or double-count — upsert
    is idempotent per symbol within a single run too."""
    duplicated = [_symbol("TCS", "1"), _symbol("TCS", "1"), _symbol("INFY", "2")]
    collector = await _collector(session_factory, FakeProvider(symbols=duplicated))

    result = await collector.collect_symbols()

    assert result.symbols_processed == 3
    async with session_factory() as session:
        rows = (await session.execute(select(Symbol).where(Symbol.symbol == "TCS"))).scalars().all()
    assert len(rows) == 1  # still one row, not two
