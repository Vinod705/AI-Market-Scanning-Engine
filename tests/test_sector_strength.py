"""Integration tests for app.analytics.sector.sector_strength against an
in-memory DB with deterministic sector fixtures."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.sector.sector_models import SectorRotationState
from app.analytics.sector.sector_strength import compute_sector_evidence
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository

_N = 80


async def _seed_sector(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    start_price: float,
    step: float,
    momentum_scores: list[float] | None = None,
    trend_strength: float | None = 60.0,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=f"IDX|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id

        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(_N)]
        candles = [
            Candle(
                timestamp=d,
                open=start_price + i * step,
                high=start_price + i * step + 1,
                low=start_price + i * step - 1,
                close=start_price + i * step,
                volume=100_000,
            )
            for i, d in enumerate(dates)
        ]
        await PriceRepository(session).upsert_daily_many(symbol_id, candles)

        if momentum_scores is not None:
            feature_repo = DailyFeatureRepository(session)
            for i, d in enumerate(dates):
                values: dict[str, object] = {"momentum_score": momentum_scores[i]}
                if trend_strength is not None:
                    values["trend_strength"] = trend_strength
                await feature_repo.upsert(symbol_id, d.date(), values)

        await session.commit()
        return symbol_id


async def test_returns_none_when_sector_symbol_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sector(session_factory, "NIFTY", start_price=100.0, step=0.3)

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NO SUCH SECTOR")

    assert evidence is None


async def test_returns_none_when_benchmark_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sector(session_factory, "NIFTY IT", start_price=100.0, step=0.9)

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NIFTY IT")

    assert evidence is None


async def test_breadth_and_volume_participation_are_always_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No sector/industry metadata exists in this project — these two
    evidence sources must never be silently fabricated."""
    await _seed_sector(session_factory, "NIFTY", start_price=100.0, step=0.3)
    momentum = [10.0 + i * 0.5 for i in range(_N)]
    await _seed_sector(
        session_factory, "NIFTY IT", start_price=100.0, step=0.9, momentum_scores=momentum
    )

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NIFTY IT")

    assert evidence is not None
    assert evidence.breadth is None
    assert evidence.volume_participation is None
    assert "breadth" in evidence.missing_evidence
    assert "volume_participation" in evidence.missing_evidence


async def test_evidence_populated_from_multiple_real_sources(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sector(session_factory, "NIFTY", start_price=100.0, step=0.3)
    momentum = [10.0 + i * 0.5 for i in range(_N)]
    await _seed_sector(
        session_factory,
        "NIFTY IT",
        start_price=100.0,
        step=0.9,
        momentum_scores=momentum,
        trend_strength=72.0,
    )

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NIFTY IT")

    assert evidence is not None
    assert evidence.sector_symbol == "NIFTY IT"
    assert evidence.benchmark_symbol == "NIFTY"
    assert evidence.rs is not None
    assert evidence.rs_ratio is not None
    assert evidence.rs_momentum is not None
    assert evidence.rotation_state is not None
    assert evidence.momentum_score is not None
    assert float(evidence.momentum_score) == pytest.approx(10.0 + (_N - 1) * 0.5, abs=0.01)
    assert evidence.trend_strength == 72
    assert evidence.price_performance_pct is not None
    assert evidence.momentum_acceleration is not None
    assert evidence.score is not None
    assert 0 <= evidence.score <= 100
    assert set(evidence.evidence_sources_used) & {
        "relative_strength",
        "momentum",
        "trend",
        "price_performance",
        "momentum_acceleration",
    }


async def test_rotation_state_matches_rrg_quadrant_mapping(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sector outperforming benchmark with rising RS should land in
    LEADING or STRENGTHENING, never LAGGING/WEAKENING."""
    await _seed_sector(session_factory, "NIFTY", start_price=100.0, step=0.1)
    momentum = [10.0 + i * 0.5 for i in range(_N)]
    await _seed_sector(
        session_factory, "NIFTY IT", start_price=100.0, step=1.2, momentum_scores=momentum
    )

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NIFTY IT")

    assert evidence is not None
    assert evidence.rotation_state in {
        SectorRotationState.LEADING,
        SectorRotationState.STRENGTHENING,
    }


async def test_no_daily_features_still_returns_evidence_with_missing_momentum(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A sector with real price history but no DailyFeature rows yet should
    still get a partial evidence read (RRG-based), not None outright."""
    await _seed_sector(session_factory, "NIFTY", start_price=100.0, step=0.3)
    await _seed_sector(session_factory, "NIFTY IT", start_price=100.0, step=0.9)

    async with session_factory() as session:
        evidence = await compute_sector_evidence(session, Settings(), "NIFTY IT")

    assert evidence is not None
    assert evidence.momentum_score is None
    assert evidence.trend_strength is None
    assert "momentum" in evidence.missing_evidence
    assert "trend" in evidence.missing_evidence
    # RRG-based evidence should still be present
    assert evidence.rs_ratio is not None
