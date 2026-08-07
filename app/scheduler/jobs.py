"""Market data collection jobs, registered onto the shared `SchedulerService`."""

from loguru import logger

from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.scheduler.service import SchedulerService

JOB_ID_INTRADAY = "market_data_collect_intraday"
JOB_ID_DAILY = "market_data_collect_daily"
JOB_ID_SYMBOL_REFRESH = "market_data_refresh_symbols"


def register_market_data_jobs(
    scheduler_service: SchedulerService,
    collector: MarketDataCollector,
    market_updater: MarketStatusUpdater,
) -> None:
    """Register the three market-data jobs described in the Phase 2 spec.

    - Every minute: intraday candles (a no-op outside market hours).
    - Daily, shortly after close: daily candles.
    - Daily, before open: symbol master refresh.
    """

    async def _intraday_job() -> None:
        if not market_updater.is_market_open():
            return
        await collector.collect_intraday()

    async def _daily_job() -> None:
        await collector.collect_daily()

    async def _symbol_refresh_job() -> None:
        await collector.collect_symbols()

    scheduler_service.add_job(
        _intraday_job,
        trigger="interval",
        minutes=1,
        id=JOB_ID_INTRADAY,
        replace_existing=True,
    )
    scheduler_service.add_job(
        _daily_job,
        trigger="cron",
        hour=16,
        minute=0,
        id=JOB_ID_DAILY,
        replace_existing=True,
    )
    scheduler_service.add_job(
        _symbol_refresh_job,
        trigger="cron",
        hour=7,
        minute=0,
        id=JOB_ID_SYMBOL_REFRESH,
        replace_existing=True,
    )

    logger.info(
        "Registered market data jobs: {intraday}, {daily}, {refresh}",
        intraday=JOB_ID_INTRADAY,
        daily=JOB_ID_DAILY,
        refresh=JOB_ID_SYMBOL_REFRESH,
    )
