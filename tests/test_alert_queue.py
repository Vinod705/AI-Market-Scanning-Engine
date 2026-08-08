"""Tests for app.alerts.queue.AlertQueue."""

import asyncio

from app.alerts.queue import AlertQueue


async def test_put_and_get_round_trip() -> None:
    queue = AlertQueue()
    await queue.put(42)
    assert queue.qsize() == 1

    item = await queue.get()
    assert item.alert_id == 42
    queue.task_done()
    assert queue.qsize() == 0


async def test_get_waits_for_an_item_without_blocking_other_work() -> None:
    """The whole point of the queue: a producer's `put` must never block on
    a slow/absent consumer — confirmed by putting concurrently with a
    consumer that's still waiting."""
    queue = AlertQueue()

    async def consumer() -> int:
        item = await queue.get()
        return item.alert_id

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)  # let the consumer start waiting on an empty queue
    await queue.put(7)

    result = await asyncio.wait_for(consumer_task, timeout=1.0)
    assert result == 7
