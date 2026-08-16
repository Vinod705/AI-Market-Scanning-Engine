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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.decision.engine import DecisionEngine
from app.features.engine import FeatureEngine
from app.pipeline.events import PipelineEvent
from app.pipeline.queue import PipelineEventQueue
from app.repositories.worker_heartbeat_repository import WorkerHeartbeatRepository
from app.scanner.engine import ScannerEngine

WORKER_NAME = "pipeline_worker"

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
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._queue = queue
        self._feature_engine = feature_engine
        self._scanner_engine = scanner_engine
        self._decision_engine = decision_engine
        self._is_market_open = is_market_open
        self._stopping = False
        # Optional — see app.sanity's module docstring. None (the default,
        # and every existing test's constructor call) simply means "don't
        # ping a heartbeat", never a behavior change to the real pipeline.
        self._session_factory = session_factory

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
                    await self._ping_heartbeat(f"processed source={event.source}")
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

    async def _ping_heartbeat(self, detail: str) -> None:
        """Read-only-adjacent: the sanity checker's only write anywhere
        in this project, and even this one is a pure liveness ping, never
        market/feature/scanner data. A heartbeat-write failure must never
        take down real pipeline processing — swallowed and logged, same
        pattern as every other "must never kill this loop" guard here."""
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                await WorkerHeartbeatRepository(session).ping_success(WORKER_NAME, detail=detail)
                await session.commit()
        except Exception:  # noqa: BLE001 - a heartbeat write must never affect real work
            logger.warning("Pipeline worker: failed to record heartbeat")

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
