"""Feature engine job, registered onto the shared `SchedulerService`.

Runs every minute like the Phase 2 market-data jobs, but `FeatureEngine`
does its own "is there anything new" check per symbol (see
`app.features.engine`), so most ticks are cheap no-ops.
"""

from loguru import logger

from app.data.market_updater import MarketStatusUpdater
from app.features.engine import FeatureEngine
from app.scheduler.service import SchedulerService

JOB_ID_FEATURES = "feature_engine_run"


def register_feature_jobs(
    scheduler_service: SchedulerService,
    feature_engine: FeatureEngine,
    market_updater: MarketStatusUpdater,
) -> None:
    """Every minute: recompute session features if the market's open, and
    daily features for any symbol whose daily candle is newer than its last
    computed feature row (this part runs regardless of market hours, so a
    fresh daily close gets picked up promptly after the Phase 2 daily job)."""

    async def _feature_job() -> None:
        if market_updater.is_market_open():
            await feature_engine.run_session()
        await feature_engine.run_daily()

    scheduler_service.add_job(
        _feature_job,
        trigger="interval",
        minutes=1,
        id=JOB_ID_FEATURES,
        replace_existing=True,
    )

    logger.info("Registered feature engine job: {job_id}", job_id=JOB_ID_FEATURES)
