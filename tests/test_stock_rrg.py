"""Integration tests for app.analytics.rrg.stock_rrg against an in-memory DB."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.rrg.stock_rrg import compute_stock_rrg
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import PriceRepository, SymbolRepository

_N = 80


async def _seed_symbol_with_history(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, start_price: float, step: float
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id

        candles = [
            Candle(
                timestamp=datetime(2026, 1, 1) + timedelta(days=i),
                open=start_price + i * step,
                high=start_price + i * step + 1,
                low=start_price + i * step - 1,
                close=start_price + i * step,
                volume=100_000,
            )
            for i in range(_N)
        ]
        await PriceRepository(session).upsert_daily_many(symbol_id, candles)
        await session.commit()
        return symbol_id


async def test_compute_stock_rrg_returns_series_for_real_symbol_and_benchmark(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_history(session_factory, "TCS", 100.0, 0.8)
    await _seed_symbol_with_history(session_factory, "NIFTY", 100.0, 0.3)

    async with session_factory() as session:
        points = await compute_stock_rrg(session, Settings(), "TCS")

    assert points is not None
    assert len(points) == _N
    assert points[0].symbol == "TCS"
    assert points[0].benchmark_symbol == "NIFTY"


async def test_compute_stock_rrg_uses_custom_benchmark_when_given(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_history(session_factory, "TCS", 100.0, 0.8)
    await _seed_symbol_with_history(session_factory, "BANKNIFTY", 100.0, 0.2)

    async with session_factory() as session:
        points = await compute_stock_rrg(session, Settings(), "TCS", benchmark_symbol="BANKNIFTY")

    assert points is not None
    assert points[0].benchmark_symbol == "BANKNIFTY"


async def test_compute_stock_rrg_returns_none_when_symbol_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_history(session_factory, "NIFTY", 100.0, 0.3)

    async with session_factory() as session:
        points = await compute_stock_rrg(session, Settings(), "NOSUCHSYMBOL")

    assert points is None


async def test_compute_stock_rrg_returns_none_when_benchmark_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_history(session_factory, "TCS", 100.0, 0.8)
    # No NIFTY symbol seeded at all.

    async with session_factory() as session:
        points = await compute_stock_rrg(session, Settings(), "TCS")

    assert points is None


async def test_compute_stock_rrg_returns_none_when_symbol_has_no_price_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOFEATURES", exchange="NSE", instrument_token="NSE_EQ|X")
        )
        await session.commit()
    await _seed_symbol_with_history(session_factory, "NIFTY", 100.0, 0.3)

    async with session_factory() as session:
        points = await compute_stock_rrg(session, Settings(), "NOFEATURES")

    assert points is None
