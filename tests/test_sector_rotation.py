"""Integration tests for app.analytics.sector.sector_rotation against an
in-memory DB with deterministic sector fixtures."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.sector.sector_rotation import compute_sector_rotation
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import PriceRepository, SymbolRepository

_N = 80


async def _seed_index(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    start_price: float,
    step: float,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=f"IDX|{symbol}")
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


async def test_sectors_grouped_by_rotation_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index(session_factory, "NIFTY", start_price=100.0, step=0.3)
    # Strongly outperforming -> should land in LEADING/STRENGTHENING.
    await _seed_index(session_factory, "NIFTY IT", start_price=100.0, step=1.5)
    # Strongly underperforming -> should land in LAGGING/WEAKENING.
    await _seed_index(session_factory, "NIFTY PHARMA", start_price=100.0, step=-1.0)

    async with session_factory() as session:
        summary = await compute_sector_rotation(
            session, Settings(), ["NIFTY IT", "NIFTY PHARMA"]
        )

    assert len(summary.sectors) == 2
    assert summary.no_data == []
    grouped = summary.leading + summary.strengthening
    lagged = summary.lagging + summary.weakening
    assert "NIFTY IT" in grouped
    assert "NIFTY PHARMA" in lagged


async def test_unknown_sector_symbol_reported_as_no_data_not_fabricated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index(session_factory, "NIFTY", start_price=100.0, step=0.3)
    await _seed_index(session_factory, "NIFTY IT", start_price=100.0, step=0.9)

    async with session_factory() as session:
        summary = await compute_sector_rotation(
            session, Settings(), ["NIFTY IT", "NOT REAL SECTOR"]
        )

    assert len(summary.sectors) == 1
    assert summary.no_data == ["NOT REAL SECTOR"]


async def test_sectors_sorted_by_score_descending(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index(session_factory, "NIFTY", start_price=100.0, step=0.2)
    await _seed_index(session_factory, "NIFTY IT", start_price=100.0, step=1.8)
    await _seed_index(session_factory, "NIFTY PHARMA", start_price=100.0, step=0.3)

    async with session_factory() as session:
        summary = await compute_sector_rotation(
            session, Settings(), ["NIFTY PHARMA", "NIFTY IT"]
        )

    scores = [e.score for e in summary.sectors]
    assert scores == sorted(scores, reverse=True)


async def test_empty_sector_list_returns_empty_summary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index(session_factory, "NIFTY", start_price=100.0, step=0.3)

    async with session_factory() as session:
        summary = await compute_sector_rotation(session, Settings(), [])

    assert summary.sectors == []
    assert summary.no_data == []
    assert summary.leading == []
