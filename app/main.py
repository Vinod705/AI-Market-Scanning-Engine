"""FastAPI application entrypoint: app factory, lifespan, and router wiring."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.api.alerts import router as alerts_router
from app.api.features import router as features_router
from app.api.health import router as health_router
from app.api.market import router as market_router
from app.api.scanner import router as scanner_router
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_exception_handlers, request_context_middleware
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, check_database_connection, dispose_engine
from app.decision.engine import DecisionEngine
from app.features.engine import FeatureEngine
from app.notifications.manager import NotificationManager
from app.notifications.whatsapp import WhatsAppProvider
from app.providers.base_provider import ProviderError
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.scheduler.alert_jobs import register_alert_jobs
from app.scheduler.feature_jobs import register_feature_jobs
from app.scheduler.jobs import register_market_data_jobs
from app.scheduler.scanner_jobs import register_scanner_jobs
from app.scheduler.service import get_scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown events."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting {app_name} ({env})", app_name=settings.app_name, env=settings.app_env)

    try:
        await check_database_connection()
        logger.info("Database connection verified")
    except Exception:
        logger.warning("Database not reachable at startup — continuing, will retry per-request")

    provider = FivePaisaProvider(settings)
    if settings.fivepaisa_configured:
        try:
            await provider.connect()
        except ProviderError as exc:
            logger.warning("5paisa provider not connected at startup: {error}", error=exc)
    else:
        logger.warning(
            "5paisa credentials not configured — market data jobs will fail until they are"
        )

    market_updater = MarketStatusUpdater(AsyncSessionLocal)
    collector = MarketDataCollector(provider, AsyncSessionLocal, market_updater)
    feature_engine = FeatureEngine(AsyncSessionLocal, settings)

    scanner_registry = ScannerRegistry()
    scanner_registry.register(BreakoutScanner(settings))
    scanner_engine = ScannerEngine(AsyncSessionLocal, scanner_registry)

    alert_queue = AlertQueue()
    alert_manager = AlertManager(AsyncSessionLocal, settings, alert_queue)
    decision_engine = DecisionEngine(AsyncSessionLocal, settings, alert_manager)

    whatsapp_provider = WhatsAppProvider(settings)
    if not settings.whatsapp_configured:
        logger.warning(
            "WhatsApp credentials not configured — alerts will be created and queued, "
            "but delivery attempts will fail until they are"
        )
    notification_manager = NotificationManager(
        AsyncSessionLocal, settings, alert_queue, whatsapp_provider
    )

    scheduler_service = get_scheduler_service(settings)
    register_market_data_jobs(scheduler_service, collector, market_updater)
    register_feature_jobs(scheduler_service, feature_engine, market_updater)
    register_scanner_jobs(scheduler_service, scanner_engine)
    register_alert_jobs(scheduler_service, decision_engine, AsyncSessionLocal)
    scheduler_service.start()

    # Restart recovery: reload PENDING/RETRYING alerts from PostgreSQL and
    # (re)drive delivery before the worker starts pulling from the (empty)
    # in-memory queue — see NotificationManager.recover_pending.
    await notification_manager.recover_pending()
    notification_worker = asyncio.create_task(notification_manager.run_forever())

    app.state.provider = provider
    app.state.scheduler_service = scheduler_service
    app.state.alert_queue = alert_queue
    app.state.whatsapp_provider = whatsapp_provider
    app.state.settings = settings

    yield

    logger.info("Shutting down {app_name}", app_name=settings.app_name)
    scheduler_service.shutdown(wait=True)
    notification_manager.stop()
    notification_worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await notification_worker
    await provider.disconnect()
    await dispose_engine()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.middleware("http")(request_context_middleware)
    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(market_router)
    app.include_router(features_router)
    app.include_router(scanner_router)
    app.include_router(alerts_router)

    return app


app = create_app()
