"""APScheduler-based scheduler service.

Phase 1 only provides the framework: start/stop lifecycle wired into the
FastAPI app, and a registry for future jobs. No jobs are registered yet.
"""

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.job import Job
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from loguru import logger

from app.config.settings import Settings


class SchedulerService:
    """Thin wrapper around AsyncIOScheduler exposing a clean start/stop API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": AsyncIOExecutor()},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
            timezone=settings.scheduler_timezone,
        )

    def start(self) -> None:
        if not self._settings.scheduler_enabled:
            logger.info("Scheduler disabled via configuration, skipping start")
            return
        self._scheduler.start()
        logger.info("Scheduler started (timezone={tz})", tz=self._settings.scheduler_timezone)

    def shutdown(self, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Scheduler stopped")

    def add_job(self, func, trigger: BaseTrigger | str, **kwargs) -> Job:
        """Register a job with the scheduler. No jobs are registered in Phase 1."""
        return self._scheduler.add_job(func, trigger, **kwargs)

    @property
    def running(self) -> bool:
        return self._scheduler.running

    @property
    def jobs(self) -> list[Job]:
        return self._scheduler.get_jobs()


_scheduler_service: SchedulerService | None = None


def get_scheduler_service(settings: Settings | None = None) -> SchedulerService:
    """Return the process-wide singleton SchedulerService, creating it on first use."""
    global _scheduler_service
    if _scheduler_service is None:
        if settings is None:
            from app.config.settings import get_settings

            settings = get_settings()
        _scheduler_service = SchedulerService(settings)
    return _scheduler_service
