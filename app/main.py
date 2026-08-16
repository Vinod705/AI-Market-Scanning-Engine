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
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.candidates import router as candidates_router
from app.api.dashboard import router as dashboard_router
from app.api.features import router as features_router
from app.api.fundamental_queue import router as fundamental_queue_router
from app.api.health import router as health_router
from app.api.market import router as market_router
from app.api.sanity import router as sanity_router
from app.api.scanner import router as scanner_router
from app.auth.redis_client import check_redis_connection, dispose_redis, redis_client
from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.ipo_intraday_scanner import IpoIntradayScanner
from app.candidates.pre_breakout_scanner import PreBreakoutScanner
from app.candidates.sources import CandidateSourceProvider, TradingViewSourceProvider
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_exception_handlers, request_context_middleware
from app.data.collector import MarketDataCollector
from app.data.ingestion_worker import IntradayIngestionWorker
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, check_database_connection, dispose_engine
from app.decision.engine import DecisionEngine
from app.features.engine import FeatureEngine
from app.fundamentals.orchestrator import MultiSourceFundamentalProvider
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_service import FundamentalQueueService
from app.fundamentals.trendlyne_provider import TrendlyneFundamentalDataProvider
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.fundamentals.upstox_fundamental_provider import UpstoxFundamentalDataProvider
from app.notifications.manager import NotificationManager
from app.notifications.router import NotificationRouter
from app.notifications.telegram import TelegramProvider
from app.pipeline.queue import RedisPipelineEventQueue
from app.pipeline.worker import PipelineWorker
from app.providers.base_provider import ProviderError
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.providers.upstox_provider import UpstoxProvider
from app.providers.upstox_websocket import UpstoxMarketFeed
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.engine import ScannerEngine
from app.scanner.momentum_scanner import MomentumScanner
from app.scanner.orb_scanner import OrbScanner
from app.scanner.scanner_registry import ScannerRegistry
from app.scanner.vcp_scanner import VcpScanner
from app.scheduler.alert_jobs import register_alert_jobs
from app.scheduler.analytics_snapshot_jobs import register_analytics_snapshot_jobs
from app.scheduler.digest_jobs import register_digest_jobs
from app.scheduler.fundamental_queue_jobs import register_fundamental_queue_jobs
from app.scheduler.jobs import register_market_data_jobs
from app.scheduler.momentum_observation_followup_jobs import (
    register_momentum_observation_followup_jobs,
)
from app.scheduler.momentum_pipeline_jobs import register_momentum_pipeline_jobs
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

    # Phase 2: Upstox is the primary MarketDataProvider by default (see
    # Settings.active_market_data_provider); FivePaisa stays fully supported
    # as the legacy/secondary option, selectable via env var. Whichever is
    # primary is the only one passed to MarketDataCollector — collector.py
    # depends only on the MarketDataProvider ABC, so this swap needs no
    # changes there.
    # Always exactly one of these two concrete classes (no third provider
    # exists) — a union, not the bare MarketDataProvider ABC, so this one
    # variable satisfies both MarketDataCollector's ABC-only needs and
    # register_universe_jobs's extra get_fno_symbol_roots() requirement
    # below without a cast.
    provider: UpstoxProvider | FivePaisaProvider
    if settings.active_market_data_provider == "upstox":
        provider = UpstoxProvider(settings)
        if settings.upstox_configured:
            try:
                await provider.connect()
            except ProviderError as exc:
                logger.warning("Upstox provider not connected at startup: {error}", error=exc)
        else:
            logger.warning(
                "Upstox access token not configured — market data jobs will fail until it is"
            )
    else:
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

    # app.scheduler.universe_jobs derives F&O underlying roots from
    # provider.get_fno_symbol_roots() (not part of the MarketDataProvider
    # ABC — see universe_jobs.py's FnoRootsProvider). Both FivePaisaProvider
    # and UpstoxProvider implement it, so whichever provider is primary
    # above is reused directly here — no second connection/instance needed.
    market_updater = MarketStatusUpdater(AsyncSessionLocal)
    collector = MarketDataCollector(provider, AsyncSessionLocal, market_updater, settings)
    feature_engine = FeatureEngine(AsyncSessionLocal, settings)

    # Pipeline: ingestion -> Redis Stream -> PipelineWorker (feature ->
    # scanner -> decision), replacing 4 independent 1-minute APScheduler
    # jobs that raced on each other's DB state — see app.data.ingestion_worker
    # and app.pipeline.worker for why. ensure_group() runs before either
    # worker task starts so there's no bootstrap race between the ingestion
    # loop publishing and the worker's consumer group existing.
    pipeline_queue = RedisPipelineEventQueue(redis_client, settings)
    try:
        await pipeline_queue.ensure_group()
    except Exception:
        logger.warning(
            "Pipeline queue group not created at startup — Redis not reachable? "
            "Ingestion/pipeline workers will keep retrying once started."
        )

    # Multi-source fundamental intelligence (Phase 7, extended Phase 9).
    # Upstox is now primary — verified live: 4 real Company Fundamentals
    # endpoints (income-statement, cash-flow, key-ratios; balance-sheet is
    # real but maps to no FundamentalData field) exist and require no
    # separate account/rate-limit budget (same Upstox access token/API
    # already used for market data). Trendlyne MCP is kept as a secondary/
    # legacy source — see its module docstring — purely for the fields
    # Upstox's fundamentals API doesn't expose at all (ownership/
    # shareholding: Promoter/FII/DII holding, pledge). Priority order
    # below means Upstox wins on any field both report; Trendlyne only
    # fills in what Upstox has no data for.
    # MultiSourceFundamentalProvider does real field-by-field priority
    # merging (not "pick one provider's whole response"), so both sources
    # contribute without either blocking the other. Falls back to the
    # honest UnavailableFundamentalDataProvider (UNKNOWN, never a
    # fabricated value) when nothing is configured at all.
    upstox_fundamental_provider: UpstoxFundamentalDataProvider | None = (
        UpstoxFundamentalDataProvider(settings, AsyncSessionLocal)
        if settings.upstox_configured
        else None
    )
    trendlyne_provider: TrendlyneFundamentalDataProvider | None = (
        TrendlyneFundamentalDataProvider(settings) if settings.trendlyne_mcp_configured else None
    )
    fundamental_sources: list[FundamentalDataProvider] = [
        provider
        for provider in (upstox_fundamental_provider, trendlyne_provider)
        if provider is not None
    ]
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
    scanner_registry.register(VcpScanner(settings))
    scanner_registry.register(MomentumScanner(settings))
    scanner_registry.register(OrbScanner(settings))
    scanner_registry.register(FnoMomentumScanner(settings, source_providers))
    scanner_registry.register(PreBreakoutScanner(settings, source_providers))
    scanner_registry.register(IpoIntradayScanner(settings, source_providers))
    scanner_engine = ScannerEngine(AsyncSessionLocal, scanner_registry, settings)

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

    # PipelineWorker: event-driven feature -> scanner -> decision, replacing
    # 3 of the 4 old independent 1-minute jobs (see module docstring).
    pipeline_worker = PipelineWorker(
        pipeline_queue,
        feature_engine,
        scanner_engine,
        decision_engine,
        market_updater.is_market_open,
        AsyncSessionLocal,
    )
    # Phase 3: when Upstox is primary AND configured, its WebSocket feed
    # becomes the continuous live-data path (see app.providers.upstox_websocket)
    # — IntradayIngestionWorker's REST sweep only needs to run as an
    # infrequent missing-data safety net at that point, not every ~60s for
    # every symbol. A settings copy with the longer interval keeps this a
    # call-site decision rather than new coupling inside ingestion_worker.py
    # itself. When WS isn't active, IntradayIngestionWorker keeps its
    # original short interval and role unchanged.
    upstox_market_feed: UpstoxMarketFeed | None = None
    ingestion_worker_settings = settings
    if settings.active_market_data_provider == "upstox" and settings.upstox_configured:
        upstox_market_feed = UpstoxMarketFeed(
            settings, collector, AsyncSessionLocal, pipeline_queue
        )
        ingestion_worker_settings = settings.model_copy(
            update={
                "market_data_ingestion_min_interval_seconds": (
                    settings.market_data_ingestion_ws_backup_interval_seconds
                )
            }
        )

    # IntradayIngestionWorker: replaces the 4th (market_data_collect_intraday)
    # with a self-pacing continuous loop instead of a fixed interval trigger.
    ingestion_worker = IntradayIngestionWorker(
        collector, market_updater.is_market_open, pipeline_queue, ingestion_worker_settings
    )

    scheduler_service = get_scheduler_service(settings)
    register_market_data_jobs(scheduler_service, collector, pipeline_queue)
    register_universe_jobs(scheduler_service, provider, AsyncSessionLocal)
    register_fundamental_queue_jobs(scheduler_service, fundamental_queue)
    register_alert_jobs(scheduler_service, AsyncSessionLocal)
    register_digest_jobs(
        scheduler_service,
        AsyncSessionLocal,
        ipo_telegram_provider,
        fno_telegram_provider,
        telegram_provider,
    )
    register_analytics_snapshot_jobs(scheduler_service, AsyncSessionLocal, settings)
    register_momentum_pipeline_jobs(
        scheduler_service, AsyncSessionLocal, settings, alert_manager, market_updater.is_market_open
    )
    register_momentum_observation_followup_jobs(scheduler_service, AsyncSessionLocal, settings)
    scheduler_service.start()

    # Restart recovery: reload PENDING/RETRYING alerts from PostgreSQL and
    # (re)drive delivery before the worker starts pulling from the (empty)
    # in-memory queue — see NotificationManager.recover_pending.
    await notification_manager.recover_pending()
    notification_worker = asyncio.create_task(notification_manager.run_forever())
    pipeline_worker_task = asyncio.create_task(pipeline_worker.run_forever())
    ingestion_worker_task = asyncio.create_task(ingestion_worker.run_forever())
    upstox_market_feed_task = (
        asyncio.create_task(upstox_market_feed.run_forever())
        if upstox_market_feed is not None
        else None
    )

    app.state.provider = provider
    app.state.scheduler_service = scheduler_service
    app.state.alert_queue = alert_queue
    app.state.telegram_provider = telegram_provider
    app.state.ipo_telegram_provider = ipo_telegram_provider
    app.state.fno_telegram_provider = fno_telegram_provider
    app.state.settings = settings
    app.state.tradingview_source = tradingview_source
    app.state.fundamental_provider = fundamental_provider
    app.state.pipeline_worker_task = pipeline_worker_task
    app.state.ingestion_worker_task = ingestion_worker_task
    app.state.upstox_market_feed_task = upstox_market_feed_task
    app.state.trendlyne_provider = trendlyne_provider
    app.state.fundamental_queue = fundamental_queue

    yield

    logger.info("Shutting down {app_name}", app_name=settings.app_name)
    scheduler_service.shutdown(wait=True)
    notification_manager.stop()
    notification_worker.cancel()
    pipeline_worker.stop()
    pipeline_worker_task.cancel()
    ingestion_worker.stop()
    ingestion_worker_task.cancel()
    background_tasks = [notification_worker, pipeline_worker_task, ingestion_worker_task]
    if upstox_market_feed is not None and upstox_market_feed_task is not None:
        upstox_market_feed.stop()
        upstox_market_feed_task.cancel()
        background_tasks.append(upstox_market_feed_task)
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
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
    app.include_router(analytics_router)
    app.include_router(candidates_router)
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(fundamental_queue_router)
    app.include_router(dashboard_router)
    # Project Sanity Check — a deliberately separate diagnostic surface,
    # never linked from or added to the trading dashboard above. See
    # app/api/sanity.py's module docstring.
    app.include_router(sanity_router)

    return app


app = create_app()
