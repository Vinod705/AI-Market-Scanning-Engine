"""Tests for app.scheduler.feature_jobs job registration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.data.market_updater import MarketStatusUpdater
from app.features.engine import FeatureEngine
from app.scheduler.feature_jobs import JOB_ID_FEATURES, register_feature_jobs
from app.scheduler.service import SchedulerService


async def test_register_feature_jobs_adds_expected_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    market_updater = MarketStatusUpdater(session_factory)
    feature_engine = FeatureEngine(session_factory, settings)

    register_feature_jobs(scheduler_service, feature_engine, market_updater)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_FEATURES in job_ids
