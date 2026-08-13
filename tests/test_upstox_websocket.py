"""Tests for app.providers.upstox_websocket.UpstoxMarketFeed.

No real Upstox credentials or network — the WebSocket layer is faked (a
minimal async object standing in for a `websockets` connection) and the
authorize REST call goes through `httpx.MockTransport`, same convention as
tests/test_upstox_provider.py. Protobuf frames are real, though: built with
the actual generated `MarketDataFeed_pb2` bindings and serialized for real,
not fabricated bytes.
"""

import asyncio
import contextlib
from datetime import UTC, datetime

import httpx
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.data.collector import CollectorRunResult
from app.database.base import Base
from app.models.intraday_price import IntradayPrice
from app.providers.base_provider import ProviderError, ProviderSymbol
from app.providers.upstox_proto import MarketDataFeed_pb2 as pb
from app.providers.upstox_websocket import UpstoxMarketFeed
from app.repositories.market_repository import SymbolRepository
from tests.test_pipeline_worker import FakePipelineEventQueue

_KEY = "NSE_EQ|INE467B01029"
_T0 = int(datetime(2026, 8, 13, 9, 15, 0, tzinfo=UTC).timestamp() * 1000)


def _feed_response_bytes(instrument_key: str, *, ltp: float, ltt_ms: int, ltq: int) -> bytes:
    response = pb.FeedResponse()
    response.type = pb.Type.live_feed
    feed = response.feeds[instrument_key]
    feed.ltpc.ltp = ltp
    feed.ltpc.ltt = ltt_ms
    feed.ltpc.ltq = ltq
    feed.ltpc.cp = ltp
    response.currentTs = ltt_ms
    return response.SerializeToString()


class _FakeCollector:
    """Stands in for MarketDataCollector — records calls, no real I/O."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_symbols: list[object] | None = None

    async def collect_intraday(self, symbols: list[object] | None = None) -> CollectorRunResult:
        self.calls += 1
        self.last_symbols = symbols
        count = len(symbols or [])
        return CollectorRunResult(symbols_processed=count, success_count=count)


class _FakeConnection:
    """Stands in for a `websockets` connection. `recv()` returns queued
    frames in order; once exhausted it hangs forever (simulating a live but
    silent connection) unless `closes=True`, in which case it raises
    ConnectionClosed instead — the two shapes the real reconnect/stale-data
    logic needs to distinguish."""

    def __init__(self, frames: list[bytes], *, closes: bool = False) -> None:
        self._frames = list(frames)
        self._closes = closes
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes:
        if self._frames:
            return self._frames.pop(0)
        if self._closes:
            raise websockets.exceptions.ConnectionClosed(None, None)
        await asyncio.Event().wait()  # hang — caller must time it out
        raise AssertionError("unreachable")

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _authorize_client(status_code: int = 200, wss_url: str = "wss://fake") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code == 200:
            return httpx.Response(
                200, json={"status": "success", "data": {"authorized_redirect_uri": wss_url}}
            )
        return httpx.Response(
            status_code,
            json={"status": "error", "errors": [{"message": "Invalid token"}]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        upstox_access_token="test-token",
        upstox_ws_ping_interval_seconds=5.0,
        upstox_ws_ping_timeout_seconds=5.0,
        upstox_ws_stale_threshold_seconds=5.0,
        upstox_ws_reconnect_backoff_seconds=0.01,
        upstox_ws_reconnect_max_backoff_seconds=0.02,
        upstox_ws_flush_interval_seconds=999.0,  # only the shutdown/exit flush should fire
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], instrument_token: str = _KEY
) -> None:
    async with session_factory() as session:
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="NSE", instrument_token=instrument_token)
        )
        await session.commit()


async def test_parses_ltpc_flushes_completed_candle_and_publishes_event(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    await _seed_symbol(session_factory)
    frames = [
        _feed_response_bytes(_KEY, ltp=100.0, ltt_ms=_T0, ltq=10),
        _feed_response_bytes(_KEY, ltp=105.0, ltt_ms=_T0 + 30_000, ltq=5),
        # Next minute -> rolls over, completing the first bucket.
        _feed_response_bytes(_KEY, ltp=110.0, ltt_ms=_T0 + 65_000, ltq=3),
    ]
    conn = _FakeConnection(frames, closes=True)
    monkeypatch.setattr(
        "app.providers.upstox_websocket.websockets.connect", lambda url, **kw: conn
    )

    collector = _FakeCollector()
    queue = FakePipelineEventQueue()
    worker = UpstoxMarketFeed(
        _settings(), collector, session_factory, queue, http_client=_authorize_client()
    )

    await worker._connect_and_stream()

    assert collector.calls == 1  # startup gap-fill backfill
    assert len(queue.published) == 1
    assert queue.published[0].source == "intraday_ws"

    async with session_factory() as session:
        rows = list((await session.execute(select(IntradayPrice))).scalars().all())
    assert len(rows) == 1  # only the completed bucket, never the still-open one
    assert float(rows[0].open) == 100.0
    assert float(rows[0].close) == 105.0
    assert rows[0].volume == 15


async def test_no_active_symbols_does_not_crash() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    worker = UpstoxMarketFeed(
        _settings(),
        _FakeCollector(),
        session_factory,
        FakePipelineEventQueue(),
        http_client=_authorize_client(),
    )
    await worker._connect_and_stream()  # no symbols -> returns early, no crash
    await engine.dispose()


async def test_authorize_failure_401_is_non_retryable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker = UpstoxMarketFeed(
        _settings(),
        _FakeCollector(),
        session_factory,
        FakePipelineEventQueue(),
        http_client=_authorize_client(status_code=401),
    )
    try:
        await worker._authorize()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is False


async def test_reconnect_after_connection_closed_resubscribes_and_backfills(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    await _seed_symbol(session_factory)
    connections = [_FakeConnection([], closes=True), _FakeConnection([], closes=True)]
    calls: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> _FakeConnection:
        calls.append(url)
        return connections[min(len(calls) - 1, len(connections) - 1)]

    monkeypatch.setattr("app.providers.upstox_websocket.websockets.connect", fake_connect)

    collector = _FakeCollector()
    worker = UpstoxMarketFeed(
        _settings(upstox_ws_reconnect_backoff_seconds=0.01, upstox_ws_reconnect_max_backoff_seconds=0.02),
        collector,
        session_factory,
        FakePipelineEventQueue(),
        http_client=_authorize_client(),
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.1)
    worker.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert len(calls) >= 2  # reconnected at least once
    assert collector.calls >= 2  # gap-fill backfill re-ran on each (re)connect


async def test_stale_connection_with_no_messages_triggers_reconnect(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    await _seed_symbol(session_factory)
    connections = [_FakeConnection([]), _FakeConnection([], closes=True)]  # first hangs
    calls: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> _FakeConnection:
        calls.append(url)
        return connections[min(len(calls) - 1, len(connections) - 1)]

    monkeypatch.setattr("app.providers.upstox_websocket.websockets.connect", fake_connect)

    worker = UpstoxMarketFeed(
        _settings(upstox_ws_stale_threshold_seconds=0.05, upstox_ws_reconnect_backoff_seconds=0.01),
        _FakeCollector(),
        session_factory,
        FakePipelineEventQueue(),
        http_client=_authorize_client(),
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.3)
    worker.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert len(calls) >= 2  # the stale (silent) first connection got reconnected


async def test_shutdown_flushes_completed_candle_but_not_open_bucket(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    await _seed_symbol(session_factory)
    frames = [
        _feed_response_bytes(_KEY, ltp=100.0, ltt_ms=_T0, ltq=10),
        _feed_response_bytes(_KEY, ltp=105.0, ltt_ms=_T0 + 30_000, ltq=5),
        # Completes the first bucket, opens a second (never-completed) one.
        _feed_response_bytes(_KEY, ltp=110.0, ltt_ms=_T0 + 65_000, ltq=3),
    ]
    conn = _FakeConnection(frames)  # hangs after the 3rd frame, doesn't close on its own
    monkeypatch.setattr(
        "app.providers.upstox_websocket.websockets.connect", lambda url, **kw: conn
    )

    worker = UpstoxMarketFeed(
        _settings(upstox_ws_stale_threshold_seconds=5.0),
        _FakeCollector(),
        session_factory,
        FakePipelineEventQueue(),
        http_client=_authorize_client(),
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.1)  # let it process all 3 queued frames
    worker.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    async with session_factory() as session:
        rows = list((await session.execute(select(IntradayPrice))).scalars().all())
    # The completed 09:15 bucket was flushed (the finally-flush safety net);
    # the still-open 09:16 bucket was never written as if it were a full bar.
    assert len(rows) == 1
    assert float(rows[0].close) == 105.0
