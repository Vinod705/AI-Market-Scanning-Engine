"""Market data collection jobs, registered onto the shared `SchedulerService`.

Intraday collection is NOT registered here anymore — see
`app.data.ingestion_worker.IntradayIngestionWorker`, a self-pacing
continuous loop that replaced its old `minutes=1` interval trigger (see
that module's docstring for why). What's left here is genuinely
periodic/maintenance-shaped, matching what APScheduler is for in this
architecture.
"""

from loguru import logger

from app.core.time import utc_now
from app.data.collector import MarketDataCollector
from app.pipeline.events import PipelineEvent
from app.pipeline.queue import PipelineEventQueue
from app.scheduler.service import SchedulerService

JOB_ID_DAILY = "market_data_collect_daily"
JOB_ID_SYMBOL_REFRESH = "market_data_refresh_symbols"


def register_market_data_jobs(
    scheduler_service: SchedulerService,
    collector: MarketDataCollector,
    queue: PipelineEventQueue,
) -> None:
    """Register the remaining two market-data jobs.

    - Daily, shortly after close: daily candles. Publishes a PipelineEvent
      on success so app.pipeline.worker.PipelineWorker picks up the new
      daily candles the same way it reacts to intraday updates.
    - Daily, before open: symbol master refresh.
    """

    async def _daily_job() -> None:
        result = await collector.collect_daily()
        if result.success_count > 0:
            await queue.publish(
                PipelineEvent(
                    source="daily", symbol_count=result.success_count, as_of=utc_now()
                )
            )

    async def _symbol_refresh_job() -> None:
        await collector.collect_symbols()

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
        "Registered market data jobs: {daily}, {refresh}",
        daily=JOB_ID_DAILY,
        refresh=JOB_ID_SYMBOL_REFRESH,
    )
