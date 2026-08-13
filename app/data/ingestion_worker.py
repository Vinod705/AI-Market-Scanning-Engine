"""IntradayIngestionWorker: replaces the old fixed-interval (`minutes=1`)
APScheduler intraday job with a self-pacing continuous loop.

The old job's failure mode: rate-limited per-symbol HTTP calls could exceed
60s, and `max_instances=1` (app.scheduler.service) then made APScheduler
silently *skip* the next trigger. This loop has no fixed trigger to skip —
if a pass finishes early it sleeps out the remainder of the target
interval; if it runs long (rate-limit bound), it just starts the next pass
immediately. Ingestion cadence becomes an honest reflection of the broker's
own throughput ceiling instead of a scheduler artifact.

`MarketDataCollector.collect_intraday()` itself is unchanged — this only
changes how/when it's called, and publishes a `PipelineEvent` so
`app.pipeline.worker.PipelineWorker` can react to fresh data instead of
polling on its own blind timer too.
"""

import asyncio
import time
from collections.abc import Callable

from loguru import logger

from app.config.settings import Settings
from app.core.time import utc_now
from app.data.collector import MarketDataCollector
from app.pipeline.events import PipelineEvent
from app.pipeline.queue import PipelineEventQueue


class IntradayIngestionWorker:
    def __init__(
        self,
        collector: MarketDataCollector,
        is_market_open: Callable[[], bool],
        queue: PipelineEventQueue,
        settings: Settings,
    ) -> None:
        self._collector = collector
        self._is_market_open = is_market_open
        self._queue = queue
        self._min_interval_seconds = settings.market_data_ingestion_min_interval_seconds
        self._closed_poll_interval_seconds = settings.market_closed_poll_interval_seconds
        self._stopping = False

    async def run_forever(self) -> None:
        logger.info("Intraday ingestion worker started")
        while not self._stopping:
            if not self._is_market_open():
                await asyncio.sleep(self._closed_poll_interval_seconds)
                continue

            started = time.monotonic()
            try:
                result = await self._collector.collect_intraday()
                if result.success_count > 0:
                    await self._queue.publish(
                        PipelineEvent(
                            source="intraday",
                            symbol_count=result.success_count,
                            as_of=utc_now(),
                        )
                    )
            except Exception:  # noqa: BLE001 - a bad pass (e.g. Redis down) must never kill this loop
                logger.exception("Intraday ingestion worker: pass failed")

            remaining = self._min_interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            # else: this pass ran longer than the target interval (rate-limit
            # bound) — loop again immediately. No fixed trigger to skip.

        logger.info("Intraday ingestion worker stopped")

    def stop(self) -> None:
        self._stopping = True
