"""Health check endpoint.

Phase 5 extends this from bare process-liveness to reporting every major
subsystem's status, per the spec — rather than a second, duplicate health
system. Components are read off `app.state` (populated during the
lifespan in `app.main`); a component that hasn't started yet (e.g. under
the test client, which doesn't run the lifespan) degrades gracefully to
"unavailable" instead of raising.
"""

from fastapi import APIRouter, Request

from app.database.session import check_database_connection
from app.notifications.telegram import TelegramProvider
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    state = request.app.state

    try:
        await check_database_connection()
        database = "healthy"
    except Exception:  # noqa: BLE001 - a health check must never itself raise
        database = "unhealthy"

    provider = getattr(state, "provider", None)
    market_data = "healthy" if provider is not None and provider.is_connected() else "degraded"

    scheduler_service = getattr(state, "scheduler_service", None)
    scheduler_running = scheduler_service is not None and scheduler_service.running
    scheduler_status = "healthy" if scheduler_running else "unavailable"

    alert_queue = getattr(state, "alert_queue", None)
    alert_queue_status = "healthy" if alert_queue is not None else "unavailable"

    async def _telegram_status(telegram_provider: TelegramProvider | None) -> str:
        if telegram_provider is None:
            return "unavailable"
        if not telegram_provider.configured:
            return "not_configured"
        return "healthy" if await telegram_provider.health_check() else "unhealthy"

    telegram = await _telegram_status(getattr(state, "telegram_provider", None))
    telegram_ipo = await _telegram_status(getattr(state, "ipo_telegram_provider", None))
    telegram_fno = await _telegram_status(getattr(state, "fno_telegram_provider", None))

    return HealthResponse(
        status="healthy" if database == "healthy" else "degraded",
        database=database,
        market_data=market_data,
        feature_engine=scheduler_status,
        scanner=scheduler_status,
        decision_engine=scheduler_status,
        alert_queue=alert_queue_status,
        telegram=telegram,
        telegram_ipo=telegram_ipo,
        telegram_fno=telegram_fno,
    )
