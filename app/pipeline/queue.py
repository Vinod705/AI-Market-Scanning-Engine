"""PipelineEventQueue: the Redis Stream connecting market-data ingestion to
the feature/scanner/decision worker.

`RedisPipelineEventQueue` is the real, production implementation — a thin
wrapper over `redis.asyncio.Redis`'s Streams commands (XADD/XGROUP
CREATE/XREADGROUP/XACK), using a consumer group so a crashed worker resumes
from its own pending-entries-list on restart instead of losing in-flight
events. `PipelineEventQueue` (the `Protocol` other code depends on) exists so
tests can substitute a hand-rolled in-memory fake — same pattern this
codebase already uses for `FakeProvider` (tests/test_collector.py) rather
than pulling in a mocking library.
"""

from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.typing import FieldT

from app.config.settings import Settings
from app.pipeline.events import PipelineEvent


class PipelineEventQueue(Protocol):
    async def ensure_group(self) -> None: ...
    async def publish(self, event: PipelineEvent) -> None: ...
    async def read(self) -> list[tuple[str, PipelineEvent]]: ...
    async def ack(self, message_id: str) -> None: ...


def _to_event(fields: dict[str, str]) -> PipelineEvent:
    return PipelineEvent(
        source=fields["source"],
        symbol_count=int(fields["symbol_count"]),
        as_of=datetime.fromisoformat(fields["as_of"]),
    )


class RedisPipelineEventQueue:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._stream_key = settings.pipeline_stream_key
        self._group = settings.pipeline_consumer_group
        self._consumer = settings.pipeline_consumer_name
        self._maxlen = settings.pipeline_stream_maxlen
        self._block_ms = settings.pipeline_stream_block_ms
        # First read() drains this consumer's own pending-entries-list
        # (events delivered but never XACK'd — e.g. the process crashed
        # mid-handling) before switching to live ">" reads. Standard
        # reliable-consumer-group pattern.
        self._draining_pending = True

    async def ensure_group(self) -> None:
        """Idempotent: safe to call on every startup, including restarts
        where the group already exists."""
        try:
            await self._redis.xgroup_create(
                self._stream_key, self._group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, event: PipelineEvent) -> None:
        fields: dict[FieldT, FieldT] = {
            "source": event.source,
            "symbol_count": str(event.symbol_count),
            "as_of": event.as_of.isoformat(),
        }
        await self._redis.xadd(
            self._stream_key, fields, maxlen=self._maxlen, approximate=True
        )

    async def read(self) -> list[tuple[str, PipelineEvent]]:
        draining = self._draining_pending
        response = await self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream_key: "0" if draining else ">"},
            count=10,
            block=None if draining else self._block_ms,
        )
        if draining:
            # An empty own-pending read means nothing was left over from a
            # previous crash — switch to live reads from here on.
            self._draining_pending = False
        if not response:
            return []
        _stream_name, entries = response[0]
        return [(message_id, _to_event(fields)) for message_id, fields in entries]

    async def ack(self, message_id: str) -> None:
        await self._redis.xack(self._stream_key, self._group, message_id)
