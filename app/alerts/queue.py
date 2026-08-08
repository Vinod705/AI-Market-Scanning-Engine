"""AlertQueue: in-memory async handoff between the Alert Manager and the
notification worker.

This is the mechanism that keeps a slow or unavailable notification
provider from ever blocking the scanner or decision engine: `AlertManager`
only ever awaits a fast in-memory `put`, never the notification call
itself. The actual send happens on a separate consumer loop
(`app.notifications.manager.NotificationManager`).
"""

import asyncio
from dataclasses import dataclass


@dataclass
class QueuedAlert:
    alert_id: int


class AlertQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedAlert] = asyncio.Queue()

    async def put(self, alert_id: int) -> None:
        await self._queue.put(QueuedAlert(alert_id=alert_id))

    async def get(self) -> QueuedAlert:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()
