"""Tests for app.scheduler.alert_jobs and app.scheduler.digest_jobs job registration.

The decision-engine run is no longer registered here — it moved into
app.pipeline.worker.PipelineWorker, see tests/test_pipeline_worker.py.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.notifications.telegram import TelegramProvider
from app.scheduler.alert_jobs import JOB_ID_ALERT_EXPIRY, register_alert_jobs
from app.scheduler.digest_jobs import (
    JOB_ID_DIGEST_EVENING,
    JOB_ID_DIGEST_MORNING,
    register_digest_jobs,
)
from app.scheduler.service import SchedulerService


async def test_register_alert_jobs_adds_expected_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    register_alert_jobs(scheduler_service, session_factory)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_ALERT_EXPIRY in job_ids


async def test_register_digest_jobs_adds_expected_jobs_at_correct_times(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    ipo_provider = TelegramProvider(settings, channel_name="telegram_ipo")
    fno_provider = TelegramProvider(settings, channel_name="telegram_fno")

    register_digest_jobs(scheduler_service, session_factory, ipo_provider, fno_provider)

    jobs_by_id = {job.id: job for job in scheduler_service.jobs}
    assert JOB_ID_DIGEST_MORNING in jobs_by_id
    assert JOB_ID_DIGEST_EVENING in jobs_by_id

    morning_fields = jobs_by_id[JOB_ID_DIGEST_MORNING].trigger.fields
    evening_fields = jobs_by_id[JOB_ID_DIGEST_EVENING].trigger.fields
    morning_hour = next(f for f in morning_fields if f.name == "hour")
    evening_hour = next(f for f in evening_fields if f.name == "hour")
    assert str(morning_hour) == "7"
    assert str(evening_hour) == "16"
