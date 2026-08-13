"""PipelineWorker: the event-driven replacement for the old independent
1-minute feature/scanner/decision APScheduler jobs.

Consumes `PipelineEvent`s published by market-data ingestion
(app.data.ingestion_worker) and runs the existing FeatureEngine ->
ScannerEngine -> DecisionEngine pipeline, unchanged, once per event instead
of on 3 more independent blind timers. Same run_forever()/stop() shape as
`app.notifications.manager.NotificationManager` — a bad event must never
kill this loop, matching that class's `except Exception: logger.exception`
pattern.
"""

import asyncio
from collections.abc import Callable

from loguru import logger

from app.decision.engine import DecisionEngine
from app.features.engine import FeatureEngine
from app.pipeline.events import PipelineEvent
from app.pipeline.queue import PipelineEventQueue
from app.scanner.engine import ScannerEngine

# Backoff after a queue-read failure (e.g. Redis transiently unreachable) so
# a persistent outage doesn't spin this loop as fast as possible.
_READ_ERROR_BACKOFF_SECONDS = 5.0


class PipelineWorker:
    def __init__(
        self,
        queue: PipelineEventQueue,
        feature_engine: FeatureEngine,
        scanner_engine: ScannerEngine,
        decision_engine: DecisionEngine,
        is_market_open: Callable[[], bool],
    ) -> None:
        self._queue = queue
        self._feature_engine = feature_engine
        self._scanner_engine = scanner_engine
        self._decision_engine = decision_engine
        self._is_market_open = is_market_open
        self._stopping = False

    async def run_forever(self) -> None:
        logger.info("Pipeline worker started")
        while not self._stopping:
            try:
                entries = await self._queue.read()
            except Exception:  # noqa: BLE001 - a queue outage must never kill this loop
                logger.exception("Pipeline worker: failed to read from queue")
                await asyncio.sleep(_READ_ERROR_BACKOFF_SECONDS)
                continue

            for message_id, event in entries:
                try:
                    await self._process(event)
                    await self._queue.ack(message_id)
                except Exception:  # noqa: BLE001 - one bad event must never kill the worker
                    logger.exception(
                        "Pipeline worker: failed to process event (source={source}, "
                        "symbol_count={count}) — left unacked for retry",
                        source=event.source,
                        count=event.symbol_count,
                    )

        logger.info("Pipeline worker stopped")

    def stop(self) -> None:
        self._stopping = True

    async def _process(self, event: PipelineEvent) -> None:
        logger.info(
            "Pipeline worker: processing event (source={source}, symbol_count={count})",
            source=event.source,
            count=event.symbol_count,
        )
        if self._is_market_open():
            await self._feature_engine.run_session()
        await self._feature_engine.run_daily()
        await self._scanner_engine.run_all()
        await self._decision_engine.run_all()
