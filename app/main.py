"""FastAPI application entrypoint: app factory, lifespan, and router wiring."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.api.admin_users import router as admin_users_router
from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.candidates import router as candidates_router
from app.api.dashboard import router as dashboard_router
from app.api.features import router as features_router
from app.api.fundamental_queue import router as fundamental_queue_router
from app.api.health import router as health_router
from app.api.market import router as market_router
from app.api.scanner import router as scanner_router
from app.auth.redis_client import check_redis_connection, dispose_redis
from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.ipo_intraday_scanner import IpoIntradayScanner
from app.candidates.pre_breakout_scanner import PreBreakoutScanner
from app.candidates.sources import CandidateSourceProvider, TradingViewSourceProvider
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_exception_handlers, request_context_middleware
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, check_database_connection, dispose_engine
from app.decision.engine import DecisionEngine
from app.features.engine import FeatureEngine
from app.fundamentals.orchestrator import MultiSourceFundamentalProvider
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_service import FundamentalQueueService
from app.fundamentals.trendlyne_provider import TrendlyneFundamentalDataProvider
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.notifications.manager import NotificationManager
from app.notifications.router import NotificationRouter
from app.notifications.telegram import TelegramProvider
from app.providers.base_provider import ProviderError
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.scheduler.alert_jobs import register_alert_jobs
from app.scheduler.digest_jobs import register_digest_jobs
from app.scheduler.feature_jobs import register_feature_jobs
from app.scheduler.fundamental_queue_jobs import register_fundamental_queue_jobs
from app.scheduler.jobs import register_market_data_jobs
from app.scheduler.scanner_jobs import register_scanner_jobs
from app.scheduler.service import get_scheduler_service
from app.scheduler.universe_jobs import register_universe_jobs


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

    try:
        await check_redis_connection()
        logger.info("Redis connection verified (session store)")
    except Exception:
        logger.warning("Redis not reachable at startup — dashboard login will fail until it is")

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

    # Multi-source fundamental intelligence (Phase 7 + follow-up). Trendlyne
    # MCP is the only source with an authorized, server-callable interface
    # — TradingView and 5paisa were both investigated and neither has one
    # (see the Phase 7 final report), so the priority list has one entry
    # today. MultiSourceFundamentalProvider still does real field-by-field
    # priority merging so a second/third source can be added here later
    # without touching the candidate/decision/alert pipeline. Falls back to
    # the honest UnavailableFundamentalDataProvider (UNKNOWN, never a
    # fabricated value) when nothing is configured at all.
    trendlyne_provider: TrendlyneFundamentalDataProvider | None = (
        TrendlyneFundamentalDataProvider(settings) if settings.trendlyne_mcp_configured else None
    )
    fundamental_sources: list[FundamentalDataProvider] = (
        [trendlyne_provider] if trendlyne_provider is not None else []
    )
    fundamental_provider: FundamentalDataProvider = (
        MultiSourceFundamentalProvider(fundamental_sources)
        if fundamental_sources
        else UnavailableFundamentalDataProvider()
    )

    # Second primary candidate-discovery source alongside 5paisa (Phase 7).
    # Always returns no candidates in this deployment — see
    # `TradingViewSourceProvider`'s docstring for exactly why (no
    # server-callable TradingView API exists here) — but is wired in for
    # real so `scanner_sources` aggregation/dedup and the health check
    # reflect the actual (currently single-source) architecture, and a
    # working provider can be substituted here without touching anything
    # else in the candidate pipeline.
    tradingview_source = TradingViewSourceProvider()
    source_providers: list[CandidateSourceProvider] = [tradingview_source]

    scanner_registry = ScannerRegistry()
    scanner_registry.register(BreakoutScanner(settings))
    scanner_registry.register(FnoMomentumScanner(settings, source_providers))
    scanner_registry.register(PreBreakoutScanner(settings, source_providers))
    scanner_registry.register(IpoIntradayScanner(settings, source_providers))
    scanner_engine = ScannerEngine(AsyncSessionLocal, scanner_registry)

    # Fundamental Queue (Phase 7.x): processes candidates in small, paced
    # batches instead of firing a Trendlyne request per scanned symbol —
    # see app/fundamentals/queue_service.py's module docstring. Runs on
    # its own scheduler job, independent of the technical scanner.
    fundamental_queue = FundamentalQueueService(AsyncSessionLocal, settings, fundamental_provider)

    alert_queue = AlertQueue()
    alert_manager = AlertManager(AsyncSessionLocal, settings, alert_queue)
    decision_engine = DecisionEngine(AsyncSessionLocal, settings, alert_manager)

    # Three independent Telegram bots: the original "default" bot (still
    # used for breakout_v1, which predates the IPO/F&O split) plus one
    # dedicated bot each for IPO and F&O candidate alerts. NotificationRouter
    # decides which one handles a given alert — see app/notifications/router.py.
    telegram_provider = TelegramProvider(settings, channel_name="telegram")
    ipo_telegram_provider = TelegramProvider(
        settings,
        bot_token=settings.ipo_telegram_bot_token,
        chat_id=settings.ipo_telegram_chat_id,
        channel_name="telegram_ipo",
    )
    fno_telegram_provider = TelegramProvider(
        settings,
        bot_token=settings.fno_telegram_bot_token,
        chat_id=settings.fno_telegram_chat_id,
        channel_name="telegram_fno",
    )
    for label, configured in (
        ("default", settings.telegram_configured),
        ("IPO", settings.ipo_telegram_configured),
        ("F&O", settings.fno_telegram_configured),
    ):
        if not configured:
            logger.warning(
                "{label} Telegram bot not configured — its alerts will be created and "
                "queued, but delivery attempts will fail until it is",
                label=label,
            )
    notification_router = NotificationRouter(
        default=telegram_provider, ipo=ipo_telegram_provider, fno=fno_telegram_provider
    )
    notification_manager = NotificationManager(
        AsyncSessionLocal, settings, alert_queue, notification_router
    )

    scheduler_service = get_scheduler_service(settings)
    register_market_data_jobs(scheduler_service, collector, market_updater)
    register_feature_jobs(scheduler_service, feature_engine, market_updater)
    register_universe_jobs(scheduler_service, provider, AsyncSessionLocal)
    register_scanner_jobs(scheduler_service, scanner_engine)
    register_fundamental_queue_jobs(scheduler_service, fundamental_queue)
    register_alert_jobs(scheduler_service, decision_engine, AsyncSessionLocal)
    register_digest_jobs(
        scheduler_service, AsyncSessionLocal, ipo_telegram_provider, fno_telegram_provider
    )
    scheduler_service.start()

    # Restart recovery: reload PENDING/RETRYING alerts from PostgreSQL and
    # (re)drive delivery before the worker starts pulling from the (empty)
    # in-memory queue — see NotificationManager.recover_pending.
    await notification_manager.recover_pending()
    notification_worker = asyncio.create_task(notification_manager.run_forever())

    app.state.provider = provider
    app.state.scheduler_service = scheduler_service
    app.state.alert_queue = alert_queue
    app.state.telegram_provider = telegram_provider
    app.state.ipo_telegram_provider = ipo_telegram_provider
    app.state.fno_telegram_provider = fno_telegram_provider
    app.state.settings = settings
    app.state.tradingview_source = tradingview_source
    app.state.fundamental_provider = fundamental_provider
    app.state.trendlyne_provider = trendlyne_provider
    app.state.fundamental_queue = fundamental_queue

    yield

    logger.info("Shutting down {app_name}", app_name=settings.app_name)
    scheduler_service.shutdown(wait=True)
    notification_manager.stop()
    notification_worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await notification_worker
    await provider.disconnect()
    await dispose_engine()
    await dispose_redis()
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
    app.include_router(candidates_router)
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(fundamental_queue_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
