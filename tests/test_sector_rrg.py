"""Integration tests for app.analytics.rrg.sector_rrg against an in-memory DB."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.rrg.sector_rrg import compute_sector_rrg
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import PriceRepository, SymbolRepository

_N = 80


async def _seed_index_with_history(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, start_price: float, step: float
) -> int:
    async with session_factory() as session:
        token_suffix = symbol.replace(" ", "_")
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=f"IDX|{token_suffix}")
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


async def test_compute_sector_rrg_returns_series_per_real_sector_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_with_history(session_factory, "NIFTY", 100.0, 0.3)
    await _seed_index_with_history(session_factory, "NIFTY IT", 100.0, 0.9)
    await _seed_index_with_history(session_factory, "NIFTY PHARMA", 100.0, 0.1)

    async with session_factory() as session:
        results = await compute_sector_rrg(
            session, Settings(), ["NIFTY IT", "NIFTY PHARMA"]
        )

    assert set(results.keys()) == {"NIFTY IT", "NIFTY PHARMA"}
    assert len(results["NIFTY IT"]) == _N
    assert results["NIFTY IT"][0].benchmark_symbol == "NIFTY"


async def test_compute_sector_rrg_skips_unknown_sector_symbols_without_fabricating(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_with_history(session_factory, "NIFTY", 100.0, 0.3)
    await _seed_index_with_history(session_factory, "NIFTY IT", 100.0, 0.9)

    async with session_factory() as session:
        results = await compute_sector_rrg(
            session, Settings(), ["NIFTY IT", "NIFTY DOES NOT EXIST"]
        )

    assert set(results.keys()) == {"NIFTY IT"}


async def test_compute_sector_rrg_returns_empty_when_benchmark_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_with_history(session_factory, "NIFTY IT", 100.0, 0.9)
    # No NIFTY benchmark seeded.

    async with session_factory() as session:
        results = await compute_sector_rrg(session, Settings(), ["NIFTY IT"])

    assert results == {}


async def test_compute_sector_rrg_with_no_sector_symbols_returns_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_with_history(session_factory, "NIFTY", 100.0, 0.3)

    async with session_factory() as session:
        results = await compute_sector_rrg(session, Settings(), [])

    assert results == {}
