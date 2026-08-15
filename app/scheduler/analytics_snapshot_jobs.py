"""Analytics snapshot job (Phase 15) — the ONLY place
`app.analytics.market.regime.compute_market_regime` and
`app.analytics.sector.sector_rotation.compute_sector_rotation` are ever
invoked on a schedule. Both are pure, already-tested Phase 7/8 functions,
reused as-is; this module adds nothing but persistence + a timer around
them.

Exists specifically so the read-only dashboard/digest never have to
compute market regime or sector/RRG state themselves — they only ever
read `market_regime_snapshots`/`sector_rrg_snapshots` (see
app.repositories.market_regime_snapshot_repository /
app.repositories.sector_rrg_snapshot_repository), keeping the read path
fully separate from any live calculation.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.regime import compute_market_regime
from app.analytics.sector.sector_rotation import compute_sector_rotation
from app.config.settings import Settings
from app.core.time import utc_now
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.scheduler.service import SchedulerService

JOB_ID_ANALYTICS_SNAPSHOT = "analytics_snapshot_run"


async def _run_snapshot(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    sector_symbols = settings.regime_sector_symbol_list
    moment = utc_now()

    async with session_factory() as session:
        regime_evidence = await compute_market_regime(
            session, settings, sector_symbols=sector_symbols or None
        )
        await MarketRegimeSnapshotRepository(session).insert(regime_evidence)

        sector_count = 0
        if sector_symbols:
            rotation_summary = await compute_sector_rotation(session, settings, sector_symbols)
            await SectorRrgSnapshotRepository(session).insert_many(
                rotation_summary.sectors, computed_at=moment
            )
            sector_count = len(rotation_summary.sectors)

        await session.commit()

    logger.info(
        "Analytics snapshot: regime={regime} score={score} sectors_snapshotted={sectors}",
        regime=regime_evidence.regime.value if regime_evidence.regime else "UNKNOWN",
        score=regime_evidence.score,
        sectors=sector_count,
    )


def register_analytics_snapshot_jobs(
    scheduler_service: SchedulerService,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async def _job() -> None:
        await _run_snapshot(session_factory, settings)

    scheduler_service.add_job(
        _job,
        trigger="interval",
        minutes=settings.analytics_snapshot_interval_minutes,
        id=JOB_ID_ANALYTICS_SNAPSHOT,
        replace_existing=True,
    )

    logger.info(
        "Registered analytics snapshot job: {job_id} (every {minutes}m, {n} sector symbol(s) configured)",
        job_id=JOB_ID_ANALYTICS_SNAPSHOT,
        minutes=settings.analytics_snapshot_interval_minutes,
        n=len(settings.regime_sector_symbol_list),
    )
