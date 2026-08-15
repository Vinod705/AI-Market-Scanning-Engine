"""Integration tests for app.analytics.market.breadth against an in-memory
DB with deterministic market fixtures."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.breadth import compute_market_breadth
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository

_DAY0 = datetime(2026, 1, 1)
_DAY1 = datetime(2026, 1, 2)  # "yesterday"
_DAY2 = datetime(2026, 1, 3)  # "today"


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    candles: list[Candle],
    feature_values: dict[str, object] | None = None,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(symbol_id, candles)
        if feature_values is not None:
            await DailyFeatureRepository(session).upsert(
                symbol_id, candles[-1].timestamp.date(), feature_values
            )
        await session.commit()
        return symbol_id


def _candle(day: datetime, *, close: float, high: float, low: float, volume: int) -> Candle:
    return Candle(timestamp=day, open=close, high=high, low=low, close=close, volume=volume)


async def test_advancing_declining_unchanged_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Advancing: close rises 100 -> 105
    await _seed_symbol(
        session_factory,
        "ADV",
        [
            _candle(_DAY1, close=100, high=101, low=99, volume=1000),
            _candle(_DAY2, close=105, high=106, low=104, volume=5000),
        ],
    )
    # Declining: close falls 100 -> 95
    await _seed_symbol(
        session_factory,
        "DEC",
        [
            _candle(_DAY1, close=100, high=101, low=99, volume=1000),
            _candle(_DAY2, close=95, high=96, low=94, volume=3000),
        ],
    )
    # Unchanged: close stays 100
    await _seed_symbol(
        session_factory,
        "FLAT",
        [
            _candle(_DAY1, close=100, high=101, low=99, volume=1000),
            _candle(_DAY2, close=100, high=100, low=100, volume=2000),
        ],
    )
    # No prior bar at all -> excluded from advance/decline counting entirely.
    await _seed_symbol(session_factory, "NOPRIOR", [_candle(_DAY2, close=50, high=51, low=49, volume=100)])

    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.advancing == 1
    assert snapshot.declining == 1
    assert snapshot.unchanged == 1
    assert snapshot.advance_decline_pct == Decimal("50")  # 1 of (1 advancing + 1 declining)
    assert snapshot.up_volume == 5000
    assert snapshot.down_volume == 3000
    assert snapshot.total_symbols == 4


async def test_pct_above_moving_averages_excludes_symbols_missing_that_ema(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Above ema20, no ema50/ema200 at all.
    await _seed_symbol(
        session_factory,
        "ABOVE20",
        [_candle(_DAY2, close=105, high=106, low=104, volume=1000)],
        feature_values={"ema20": Decimal("90")},
    )
    # Below ema20, above ema50.
    await _seed_symbol(
        session_factory,
        "MIXED",
        [_candle(_DAY2, close=95, high=96, low=94, volume=1000)],
        feature_values={"ema20": Decimal("100"), "ema50": Decimal("90")},
    )

    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.symbols_with_ema20 == 2
    assert snapshot.pct_above_ema20 == Decimal("50")  # 1 of 2 (ABOVE20 only)
    assert snapshot.symbols_with_ema50 == 1
    assert snapshot.pct_above_ema50 == Decimal("100")  # 1 of 1 (MIXED)
    assert snapshot.symbols_with_ema200 == 0
    assert snapshot.pct_above_ema200 is None


async def test_new_highs_and_lows_use_the_real_52_week_window_not_just_todays_bar(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A prior day's high (150) exceeds today's high (105), and a different
    # prior day's low (90) is below today's low (95) -> neither a new high
    # nor a new low.
    await _seed_symbol(
        session_factory,
        "NEITHER",
        [
            _candle(_DAY0, close=140, high=150, low=100, volume=1000),
            _candle(_DAY1, close=108, high=110, low=90, volume=1000),
            _candle(_DAY2, close=104, high=105, low=95, volume=1000),
        ],
    )
    # Today's low (40) is below every prior day's low -> genuine new low.
    await _seed_symbol(
        session_factory,
        "NEWLOW",
        [
            _candle(_DAY0, close=60, high=61, low=50, volume=1000),
            _candle(_DAY1, close=46, high=47, low=45, volume=1000),
            _candle(_DAY2, close=41, high=42, low=40, volume=1000),
        ],
    )

    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.symbols_with_52wk_range == 2
    assert snapshot.new_highs == 0
    assert snapshot.new_lows == 1
    # net = (0 - 1) / 2 = -0.5 -> 50 + (-0.5)*50 = 25
    assert snapshot.new_highs_lows_net_pct == Decimal("25")


async def test_vwap_is_never_fabricated_from_sparse_session_feature_coverage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "A", [_candle(_DAY2, close=100, high=101, low=99, volume=1000)])
    await _seed_symbol(session_factory, "B", [_candle(_DAY2, close=50, high=51, low=49, volume=1000)])

    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.pct_above_vwap is None
    assert snapshot.vwap_coverage_symbols == 0
    assert snapshot.vwap_coverage_total == 2


async def test_snapshot_is_timestamped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "A", [_candle(_DAY2, close=100, high=101, low=99, volume=1000)])

    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.as_of == _DAY2.date()
    assert snapshot.computed_at is not None


async def test_empty_universe_returns_none_percentages_not_zero_division(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        snapshot = await compute_market_breadth(session)

    assert snapshot.total_symbols == 0
    assert snapshot.advance_decline_pct is None
    assert snapshot.pct_above_ema20 is None
    assert snapshot.new_highs_lows_net_pct is None
