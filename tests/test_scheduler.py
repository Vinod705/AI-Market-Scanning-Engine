"""Tests for app.scheduler.jobs job registration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.scheduler.jobs import (
    JOB_ID_DAILY,
    JOB_ID_INTRADAY,
    JOB_ID_SYMBOL_REFRESH,
    register_market_data_jobs,
)
from app.scheduler.service import SchedulerService
from tests.test_collector import FakeProvider


async def test_register_market_data_jobs_adds_expected_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    market_updater = MarketStatusUpdater(session_factory)
    collector = MarketDataCollector(FakeProvider(), session_factory, market_updater)

    register_market_data_jobs(scheduler_service, collector, market_updater)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert job_ids == {JOB_ID_INTRADAY, JOB_ID_DAILY, JOB_ID_SYMBOL_REFRESH}


async def test_register_market_data_jobs_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    market_updater = MarketStatusUpdater(session_factory)
    collector = MarketDataCollector(FakeProvider(), session_factory, market_updater)

    # APScheduler queues add_job calls until the scheduler starts, so
    # replace_existing only dedupes against the live jobstore — start it
    # between registrations to exercise that path, matching how re-running
    # registration against an already-started scheduler would behave.
    register_market_data_jobs(scheduler_service, collector, market_updater)
    scheduler_service.start()
    register_market_data_jobs(scheduler_service, collector, market_updater)  # replace_existing=True

    try:
        assert len(scheduler_service.jobs) == 3
    finally:
        scheduler_service.shutdown(wait=False)
