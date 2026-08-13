"""Decision + alert engine jobs, registered onto the shared `SchedulerService`.

Two jobs, both every minute like the Phase 3/4 jobs:

- The decision job evaluates qualified scanner candidates and hands
  ALERT-grade ones to `AlertManager` (which persists, dedupes, and queues
  them — see `app.alerts.manager`).
- The expiry job marks PENDING/RETRYING alerts past `expires_at` as
  EXPIRED, which is what allows a materially-unchanged signal to raise a
  fresh alert later (see `app.models.alert.Alert`'s docstring).
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.decision.engine import DecisionEngine
from app.repositories.alert_repository import AlertEventRepository, AlertRepository
from app.scheduler.service import SchedulerService

JOB_ID_DECISION = "decision_engine_run"
JOB_ID_ALERT_EXPIRY = "alert_expiry_run"


def register_alert_jobs(
    scheduler_service: SchedulerService,
    decision_engine: DecisionEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _decision_job() -> None:
        await decision_engine.run_all()

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
        _decision_job, trigger="interval", minutes=1, id=JOB_ID_DECISION, replace_existing=True
    )
    scheduler_service.add_job(
        _expiry_job, trigger="interval", minutes=1, id=JOB_ID_ALERT_EXPIRY, replace_existing=True
    )

    logger.info(
        "Registered alert engine jobs: {decision}, {expiry}",
        decision=JOB_ID_DECISION,
        expiry=JOB_ID_ALERT_EXPIRY,
    )
