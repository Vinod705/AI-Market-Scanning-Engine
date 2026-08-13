"""Tests for app.scheduler.jobs job registration.

Intraday collection is no longer registered here — see
tests/test_ingestion_worker.py for its replacement, the self-pacing
IntradayIngestionWorker loop.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.scheduler.jobs import JOB_ID_DAILY, JOB_ID_SYMBOL_REFRESH, register_market_data_jobs
from app.scheduler.service import SchedulerService
from tests.test_collector import FakeProvider
from tests.test_pipeline_worker import FakePipelineEventQueue


async def test_register_market_data_jobs_adds_expected_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    market_updater = MarketStatusUpdater(session_factory)
    collector = MarketDataCollector(FakeProvider(), session_factory, market_updater)

    register_market_data_jobs(scheduler_service, collector, FakePipelineEventQueue())

    job_ids = {job.id for job in scheduler_service.jobs}
    assert job_ids == {JOB_ID_DAILY, JOB_ID_SYMBOL_REFRESH}


async def test_daily_job_publishes_event_after_successful_collection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    market_updater = MarketStatusUpdater(session_factory)
    provider = FakeProvider()
    collector = MarketDataCollector(provider, session_factory, market_updater)
    await collector.collect_symbols()  # seed 2 active symbols (TCS, INFY)
    queue = FakePipelineEventQueue()

    register_market_data_jobs(scheduler_service, collector, queue)
    daily_job = next(job for job in scheduler_service.jobs if job.id == JOB_ID_DAILY)
    await daily_job.func()

    assert len(queue.published) == 1
    assert queue.published[0].source == "daily"
    assert queue.published[0].symbol_count == 2


async def test_daily_job_does_not_publish_when_nothing_succeeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    market_updater = MarketStatusUpdater(session_factory)
    collector = MarketDataCollector(FakeProvider(), session_factory, market_updater)
    # No collect_symbols() -> zero active symbols -> zero success_count.
    queue = FakePipelineEventQueue()

    register_market_data_jobs(scheduler_service, collector, queue)
    daily_job = next(job for job in scheduler_service.jobs if job.id == JOB_ID_DAILY)
    await daily_job.func()

    assert queue.published == []


async def test_register_market_data_jobs_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    market_updater = MarketStatusUpdater(session_factory)
    collector = MarketDataCollector(FakeProvider(), session_factory, market_updater)
    queue = FakePipelineEventQueue()

    # APScheduler queues add_job calls until the scheduler starts, so
    # replace_existing only dedupes against the live jobstore — start it
    # between registrations to exercise that path, matching how re-running
    # registration against an already-started scheduler would behave.
    register_market_data_jobs(scheduler_service, collector, queue)
    scheduler_service.start()
    register_market_data_jobs(scheduler_service, collector, queue)  # replace_existing=True

    try:
        assert len(scheduler_service.jobs) == 2
    finally:
        scheduler_service.shutdown(wait=False)
