"""Tests for app.scheduler.alert_jobs job registration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.decision.engine import DecisionEngine
from app.scheduler.alert_jobs import JOB_ID_ALERT_EXPIRY, JOB_ID_DECISION, register_alert_jobs
from app.scheduler.service import SchedulerService


async def test_register_alert_jobs_adds_expected_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    alert_manager = AlertManager(session_factory, settings, AlertQueue())
    decision_engine = DecisionEngine(session_factory, settings, alert_manager)

    register_alert_jobs(scheduler_service, decision_engine, session_factory)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_DECISION in job_ids
    assert JOB_ID_ALERT_EXPIRY in job_ids
