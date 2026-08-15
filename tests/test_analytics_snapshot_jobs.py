"""Tests for app.scheduler.analytics_snapshot_jobs — the ONLY scheduled
caller of `compute_market_regime`/`compute_sector_rotation` (Phase 15).
Verifies real computed evidence lands in the two snapshot tables the
dashboard/digest read, using the actual (unmodified) Phase 7/8 engines
against seeded data — not a mocked compute step."""

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.scheduler.analytics_snapshot_jobs import _run_snapshot
from app.scheduler.service import SchedulerService


async def _seed_index_and_sector(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Enough price/feature history for both the benchmark index (used by
    market regime) and one sector index (used by sector rotation) to
    produce real, non-MISSING evidence."""
    async with session_factory() as session:
        symbol_repo = SymbolRepository(session)
        price_repo = PriceRepository(session)
        feature_repo = DailyFeatureRepository(session)

        for name in ("NIFTY 50", "NIFTY IT"):
            row = await symbol_repo.upsert(
                ProviderSymbol(symbol=name, exchange="NSE", instrument_token=f"NSE_INDEX|{name}")
            )
            await session.commit()

            candles = [
                Candle(
                    timestamp=datetime(2026, 1, d),
                    open=100 + d,
                    high=105 + d,
                    low=99 + d,
                    close=100 + d,
                    volume=100_000,
                )
                for d in range(1, 30)
            ]
            await price_repo.upsert_daily_many(row.id, candles)
            for d in range(1, 30):
                await feature_repo.upsert(
                    row.id,
                    date(2026, 1, d),
                    {
                        "trend_direction": "up",
                        "trend_strength": 70,
                        "bb_width": 2.0 + (d % 5) * 0.1,
                        "momentum_score": 60,
                    },
                )
            await session.commit()


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "feature_rs_benchmark_symbol": "NIFTY 50",
        "regime_sector_symbols": "NIFTY IT",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


async def test_run_snapshot_persists_market_regime_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_and_sector(session_factory)
    settings = _settings()

    await _run_snapshot(session_factory, settings)

    async with session_factory() as session:
        latest = await MarketRegimeSnapshotRepository(session).get_latest()

    assert latest is not None
    assert latest.index_symbol == "NIFTY 50"


async def test_run_snapshot_persists_sector_rrg_rows_when_symbols_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index_and_sector(session_factory)
    settings = _settings()

    await _run_snapshot(session_factory, settings)

    async with session_factory() as session:
        rows = await SectorRrgSnapshotRepository(session).get_latest_batch()

    assert any(row.sector_symbol == "NIFTY IT" for row in rows)


async def test_run_snapshot_skips_sector_rrg_when_no_symbols_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No fixed sector list is invented — an empty configuration must
    degrade to "no sector snapshot rows," not a guessed default list."""
    await _seed_index_and_sector(session_factory)
    settings = _settings(regime_sector_symbols="")

    await _run_snapshot(session_factory, settings)

    async with session_factory() as session:
        rows = await SectorRrgSnapshotRepository(session).get_latest_batch()

    assert rows == []


async def test_register_analytics_snapshot_jobs_adds_the_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.scheduler.analytics_snapshot_jobs import (
        JOB_ID_ANALYTICS_SNAPSHOT,
        register_analytics_snapshot_jobs,
    )

    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    register_analytics_snapshot_jobs(scheduler_service, session_factory, settings)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_ANALYTICS_SNAPSHOT in job_ids
