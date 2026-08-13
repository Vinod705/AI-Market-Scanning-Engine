"""Health check endpoint.

Phase 5 extends this from bare process-liveness to reporting every major
subsystem's status, per the spec — rather than a second, duplicate health
system. Components are read off `app.state` (populated during the
lifespan in `app.main`); a component that hasn't started yet (e.g. under
the test client, which doesn't run the lifespan) degrades gracefully to
"unavailable" instead of raising.
"""

from fastapi import APIRouter, Request

from app.core.system_metrics import get_system_metrics
from app.database.session import check_database_connection
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_service import FundamentalQueueService
from app.fundamentals.trendlyne_provider import TrendlyneFundamentalDataProvider
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

    # feature/scanner/decision all run inside the same PipelineWorker task
    # now (see app.pipeline.worker) rather than as independent scheduler
    # jobs, so they share one real liveness signal instead of each blindly
    # proxying "is APScheduler running at all".
    pipeline_worker_task = getattr(state, "pipeline_worker_task", None)
    pipeline_worker_status = (
        "healthy"
        if pipeline_worker_task is not None and not pipeline_worker_task.done()
        else "unavailable"
    )

    ingestion_worker_task = getattr(state, "ingestion_worker_task", None)
    ingestion_worker_status = (
        "healthy"
        if ingestion_worker_task is not None and not ingestion_worker_task.done()
        else "unavailable"
    )

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

    # Phase 7: TradingView has no server-callable interface in this
    # deployment (see app.candidates.sources.TradingViewSourceProvider) —
    # reported honestly rather than as "healthy" for a connection that
    # doesn't exist.
    tradingview = "not_configured" if getattr(state, "tradingview_source", None) else "unavailable"

    async def _trendlyne_status(provider: FundamentalDataProvider | None) -> str:
        if not isinstance(provider, TrendlyneFundamentalDataProvider):
            return "not_configured"
        return "healthy" if await provider.health_check() else "unhealthy"

    trendlyne_mcp = await _trendlyne_status(getattr(state, "trendlyne_provider", None))

    fundamental_queue = "not_configured"
    queue_service = getattr(state, "fundamental_queue", None)
    if isinstance(queue_service, FundamentalQueueService):
        try:
            summary = await queue_service.get_status_summary()
            fundamental_queue = "rate_limited" if summary.is_paused else "healthy"
        except Exception:  # noqa: BLE001 - a health check must never itself raise
            fundamental_queue = "unhealthy"

    metrics = await get_system_metrics()

    return HealthResponse(
        status="healthy" if database == "healthy" else "degraded",
        database=database,
        market_data=market_data,
        scheduler=scheduler_status,
        feature_engine=pipeline_worker_status,
        scanner=pipeline_worker_status,
        decision_engine=pipeline_worker_status,
        ingestion_worker=ingestion_worker_status,
        alert_queue=alert_queue_status,
        telegram=telegram,
        telegram_ipo=telegram_ipo,
        telegram_fno=telegram_fno,
        tradingview=tradingview,
        trendlyne_mcp=trendlyne_mcp,
        fundamental_queue=fundamental_queue,
        cpu_percent=metrics.cpu_percent,
        memory_used_mb=metrics.memory_used_mb,
        memory_total_mb=metrics.memory_total_mb,
        memory_percent=metrics.memory_percent,
        disk_used_gb=metrics.disk_used_gb,
        disk_total_gb=metrics.disk_total_gb,
        disk_percent=metrics.disk_percent,
    )
