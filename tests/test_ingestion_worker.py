"""Tests for app.data.ingestion_worker.IntradayIngestionWorker — the
self-pacing continuous loop that replaced the old fixed `minutes=1`
APScheduler intraday job."""

import asyncio

from app.config.settings import Settings
from app.data.collector import CollectorRunResult
from app.data.ingestion_worker import IntradayIngestionWorker
from app.pipeline.events import PipelineEvent
from tests.test_pipeline_worker import FakePipelineEventQueue


class _FakeCollector:
    def __init__(self, results: list[CollectorRunResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def collect_intraday(self) -> CollectorRunResult:
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]


class _RaisingPublishQueue(FakePipelineEventQueue):
    async def publish(self, event: PipelineEvent) -> None:
        raise RuntimeError("redis down")


def _settings() -> Settings:
    # Tiny intervals so the loop's self-pacing/poll sleeps don't slow the
    # test down or outlast worker.stop()'s flag-check-based exit.
    return Settings(
        market_data_ingestion_min_interval_seconds=0.01,
        market_closed_poll_interval_seconds=0.01,
    )


async def _run_briefly(worker: IntradayIngestionWorker, seconds: float = 0.05) -> None:
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(seconds)
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)


async def test_publishes_event_after_successful_pass_when_market_open() -> None:
    queue = FakePipelineEventQueue()
    collector = _FakeCollector(
        [CollectorRunResult(symbols_processed=3, success_count=3, failed_count=0)]
    )
    worker = IntradayIngestionWorker(collector, lambda: True, queue, _settings())

    await _run_briefly(worker)

    assert collector.calls >= 1
    assert len(queue.published) >= 1
    assert queue.published[0].source == "intraday"
    assert queue.published[0].symbol_count == 3


async def test_no_publish_when_zero_symbols_succeeded() -> None:
    queue = FakePipelineEventQueue()
    collector = _FakeCollector(
        [CollectorRunResult(symbols_processed=0, success_count=0, failed_count=0)]
    )
    worker = IntradayIngestionWorker(collector, lambda: True, queue, _settings())

    await _run_briefly(worker, seconds=0.03)

    assert queue.published == []


async def test_no_collection_attempted_when_market_closed() -> None:
    queue = FakePipelineEventQueue()
    collector = _FakeCollector(
        [CollectorRunResult(symbols_processed=1, success_count=1, failed_count=0)]
    )
    worker = IntradayIngestionWorker(collector, lambda: False, queue, _settings())

    await _run_briefly(worker, seconds=0.03)

    assert collector.calls == 0


async def test_publish_failure_does_not_kill_the_loop() -> None:
    queue = _RaisingPublishQueue()
    collector = _FakeCollector(
        [CollectorRunResult(symbols_processed=1, success_count=1, failed_count=0)]
    )
    worker = IntradayIngestionWorker(collector, lambda: True, queue, _settings())

    await _run_briefly(worker)

    # Kept looping and kept calling collect_intraday despite publish()
    # raising every time — a transient Redis outage must never silently
    # stall ingestion.
    assert collector.calls >= 2
