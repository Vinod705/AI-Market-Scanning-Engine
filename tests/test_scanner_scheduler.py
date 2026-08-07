"""Tests for app.scheduler.scanner_jobs job registration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.scheduler.scanner_jobs import JOB_ID_SCANNER, register_scanner_jobs
from app.scheduler.service import SchedulerService


async def test_register_scanner_jobs_adds_expected_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    registry = ScannerRegistry()
    registry.register(BreakoutScanner(settings))
    scanner_engine = ScannerEngine(session_factory, registry)

    register_scanner_jobs(scheduler_service, scanner_engine)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_SCANNER in job_ids
