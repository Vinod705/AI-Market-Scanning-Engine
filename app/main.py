"""FastAPI application entrypoint: app factory, lifespan, and router wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.api.features import router as features_router
from app.api.health import router as health_router
from app.api.market import router as market_router
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_exception_handlers, request_context_middleware
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, check_database_connection, dispose_engine
from app.features.engine import FeatureEngine
from app.providers.base_provider import ProviderError
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.scheduler.feature_jobs import register_feature_jobs
from app.scheduler.jobs import register_market_data_jobs
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

    scheduler_service = get_scheduler_service(settings)
    register_market_data_jobs(scheduler_service, collector, market_updater)
    register_feature_jobs(scheduler_service, feature_engine, market_updater)
    scheduler_service.start()

    yield

    logger.info("Shutting down {app_name}", app_name=settings.app_name)
    scheduler_service.shutdown(wait=True)
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

    return app


app = create_app()
