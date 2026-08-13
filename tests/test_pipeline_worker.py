"""Tests for app.pipeline.worker.PipelineWorker — the event-driven
replacement for the old independent feature/scanner/decision-engine
scheduler jobs."""

import asyncio
from datetime import UTC, datetime

from app.pipeline.events import PipelineEvent
from app.pipeline.worker import PipelineWorker


class FakePipelineEventQueue:
    """In-memory stand-in for RedisPipelineEventQueue — same shape this
    codebase already uses for external dependencies in tests (see
    `FakeProvider` in tests/test_collector.py), not a mocking library."""

    def __init__(self) -> None:
        self.published: list[PipelineEvent] = []
        self.acked: list[str] = []
        self._pending: list[tuple[str, PipelineEvent]] = []
        self._next_id = 1

    async def ensure_group(self) -> None:
        pass

    async def publish(self, event: PipelineEvent) -> None:
        self.published.append(event)
        self._pending.append((str(self._next_id), event))
        self._next_id += 1

    async def read(self) -> list[tuple[str, PipelineEvent]]:
        # A real XREADGROUP BLOCK yields to the event loop via network I/O;
        # this fake has no I/O, so it must yield explicitly or a
        # `while not self._stopping` loop against an always-empty queue
        # would starve every other task, including this test's own.
        await asyncio.sleep(0)
        entries, self._pending = self._pending, []
        return entries

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)


class _FakeFeatureEngine:
    def __init__(self) -> None:
        self.session_calls = 0
        self.daily_calls = 0

    async def run_session(self) -> None:
        self.session_calls += 1

    async def run_daily(self) -> None:
        self.daily_calls += 1


class _FakeScannerEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def run_all(self) -> None:
        self.calls += 1


class _FakeDecisionEngine:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls = 0
        self._raises = raises

    async def run_all(self) -> None:
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")


def _event() -> PipelineEvent:
    return PipelineEvent(source="intraday", symbol_count=3, as_of=datetime.now(UTC))


async def _run_until_idle(worker: PipelineWorker) -> None:
    """Runs the worker loop until the queue is drained, then stops it —
    avoids depending on real timing/sleeps in a hot loop test."""
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)


async def test_processes_event_runs_session_when_market_open_then_acks() -> None:
    queue = FakePipelineEventQueue()
    feature_engine = _FakeFeatureEngine()
    scanner_engine = _FakeScannerEngine()
    decision_engine = _FakeDecisionEngine()
    worker = PipelineWorker(queue, feature_engine, scanner_engine, decision_engine, lambda: True)
    await queue.publish(_event())

    await _run_until_idle(worker)

    assert feature_engine.session_calls == 1
    assert feature_engine.daily_calls == 1
    assert scanner_engine.calls == 1
    assert decision_engine.calls == 1
    assert queue.acked == ["1"]


async def test_skips_session_features_when_market_closed() -> None:
    queue = FakePipelineEventQueue()
    feature_engine = _FakeFeatureEngine()
    scanner_engine = _FakeScannerEngine()
    decision_engine = _FakeDecisionEngine()
    worker = PipelineWorker(queue, feature_engine, scanner_engine, decision_engine, lambda: False)
    await queue.publish(_event())

    await _run_until_idle(worker)

    assert feature_engine.session_calls == 0
    assert feature_engine.daily_calls == 1  # unconditional regardless of market hours


async def test_failed_event_is_not_acked_and_worker_keeps_running() -> None:
    queue = FakePipelineEventQueue()
    feature_engine = _FakeFeatureEngine()
    scanner_engine = _FakeScannerEngine()
    decision_engine = _FakeDecisionEngine(raises=True)
    worker = PipelineWorker(queue, feature_engine, scanner_engine, decision_engine, lambda: True)
    await queue.publish(_event())

    await _run_until_idle(worker)

    assert decision_engine.calls == 1
    assert queue.acked == []  # left unacked for retry, per the never-die contract


async def test_stop_ends_run_forever_promptly_with_no_events() -> None:
    queue = FakePipelineEventQueue()
    worker = PipelineWorker(
        queue, _FakeFeatureEngine(), _FakeScannerEngine(), _FakeDecisionEngine(), lambda: True
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.01)
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert task.done()
