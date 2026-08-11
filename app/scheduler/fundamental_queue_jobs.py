"""Fundamental Queue job, registered onto the shared `SchedulerService`.

Runs independently of `scanner_engine_run` (see `app.scheduler.scanner_jobs`)
— the whole point of decoupling the queue from the technical scanner (see
`app.fundamentals.queue_service`'s module docstring) is that a slow,
rate-limited Trendlyne backlog never blocks candidate discovery. The
scheduler's own `max_instances=1` default (see `app.scheduler.service`)
prevents two queue runs overlapping if one is still working through a
long backlog when the next tick fires.
"""

from loguru import logger

from app.fundamentals.queue_service import FundamentalQueueService
from app.scheduler.service import SchedulerService

JOB_ID_FUNDAMENTAL_QUEUE = "fundamental_queue_run"


def register_fundamental_queue_jobs(
    scheduler_service: SchedulerService, queue_service: FundamentalQueueService
) -> None:
    async def _fundamental_queue_job() -> None:
        await queue_service.run_queue()

    scheduler_service.add_job(
        _fundamental_queue_job,
        trigger="interval",
        minutes=5,
        id=JOB_ID_FUNDAMENTAL_QUEUE,
        replace_existing=True,
    )

    logger.info("Registered fundamental queue job: {job_id}", job_id=JOB_ID_FUNDAMENTAL_QUEUE)
