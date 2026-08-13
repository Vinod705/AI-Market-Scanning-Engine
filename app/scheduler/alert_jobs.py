"""Alert-expiry job, registered onto the shared `SchedulerService`.

The decision-engine run is NOT registered here anymore — it now runs as
part of `app.pipeline.worker.PipelineWorker`'s per-event pipeline, right
after the scanner engine, instead of on its own independent 1-minute timer
(see that module's docstring). What's left is genuine periodic cleanup:
marking PENDING/RETRYING alerts past `expires_at` as EXPIRED, which is what
allows a materially-unchanged signal to raise a fresh alert later (see
`app.models.alert.Alert`'s docstring).
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.repositories.alert_repository import AlertEventRepository, AlertRepository
from app.scheduler.service import SchedulerService

JOB_ID_ALERT_EXPIRY = "alert_expiry_run"


def register_alert_jobs(
    scheduler_service: SchedulerService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _expiry_job() -> None:
        now = utc_now()
        async with session_factory() as session:
            alert_repo = AlertRepository(session)
            event_repo = AlertEventRepository(session)
            expired = await alert_repo.expire_stale(now=now)
            for alert in expired:
                await event_repo.log(alert_id=alert.id, event_type="EXPIRED")
            if expired:
                await session.commit()
                logger.info("Expired {count} stale alert(s)", count=len(expired))

    scheduler_service.add_job(
        _expiry_job, trigger="interval", minutes=1, id=JOB_ID_ALERT_EXPIRY, replace_existing=True
    )

    logger.info("Registered alert engine job: {expiry}", expiry=JOB_ID_ALERT_EXPIRY)
