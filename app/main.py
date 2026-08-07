"""FastAPI application entrypoint: app factory, lifespan, and router wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.api.health import router as health_router
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_exception_handlers, request_context_middleware
from app.database.session import check_database_connection, dispose_engine
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

    scheduler_service = get_scheduler_service(settings)
    scheduler_service.start()

    yield

    logger.info("Shutting down {app_name}", app_name=settings.app_name)
    scheduler_service.shutdown(wait=True)
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

    return app


app = create_app()
