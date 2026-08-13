"""UpstoxMarketFeed: live ingestion via Upstox's WebSocket Market Data Feed v3.

Replaces continuous per-symbol REST polling as the primary live-data path
(see `app.data.ingestion_worker.IntradayIngestionWorker`'s reduced role once
this is active). Subscribes in `ltpc` mode only — the LTPC protobuf message
has no OI/greeks/depth field at all, which structurally satisfies "no OI
yet" rather than subscribing to a richer mode and discarding fields.

Same `run_forever()`/`stop()` shape as `IntradayIngestionWorker`/
`PipelineWorker`: a never-die outer loop, `asyncio.CancelledError`-safe
shutdown.

Connection flow (verified against Upstox's official docs and the real,
compiled `.proto` schema this session — see `app/providers/upstox_proto/`):
`GET /v3/feed/market-data-feed/authorize` (Bearer auth) returns
`data.authorized_redirect_uri`, a pre-authorized `wss://` URL (the auth
context travels in that URL's own query params — no separate Authorization
header needed on the WebSocket handshake itself). After connecting, one
JSON subscribe message is sent; the server then pushes binary Protobuf
`FeedResponse` frames.

Ticks never get written to Postgres one at a time. `TickAggregator`
(`app.providers.tick_aggregator`) turns them into completed 1-minute
`Candle`s in memory; a periodic flush (not per-tick) batch-writes those via
the same `PriceRepository.upsert_intraday_many` the REST path already uses,
then publishes one `PipelineEvent(source="intraday_ws")` on the same Redis
Stream `app.pipeline.worker.PipelineWorker` already consumes — deliberately
reusing Phase 1's existing event-driven mechanism rather than adding a
second Redis hop. `FeatureEngine`/`ScannerEngine`/`DecisionEngine` need zero
changes: they already only ever read from Postgres, never from a raw
provider payload.

Known limitation: Upstox's WebSocket feed has no replay/resume. Any trades
during a disconnect are gone from the WS path's perspective — the fix here
is not a gap detector, it's avoidance: every successful (re)connect
immediately triggers a REST backfill (`MarketDataCollector.collect_intraday`)
for exactly the symbols just (re)subscribed, covering whatever window (if
any) was actually missed. This is the same mechanism Upstox docs would call
"startup recovery," just also run on every reconnect, not only once.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime

import httpx
import websockets
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import utc_now
from app.data.collector import MarketDataCollector
from app.data.validator import DataValidator
from app.models.market_data_feed_log import MarketDataFeedLog
from app.models.symbol import Symbol
from app.pipeline.events import PipelineEvent
from app.pipeline.queue import PipelineEventQueue
from app.providers.base_provider import Candle, ProviderError
from app.providers.tick_aggregator import TickAggregator
from app.providers.upstox_proto import MarketDataFeed_pb2 as pb
from app.repositories.market_repository import (
    MarketDataFeedLogRepository,
    PriceRepository,
    SymbolRepository,
)


class UpstoxMarketFeed:
    def __init__(
        self,
        settings: Settings,
        collector: MarketDataCollector,
        session_factory: async_sessionmaker[AsyncSession],
        queue: PipelineEventQueue,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._collector = collector
        self._session_factory = session_factory
        self._queue = queue
        self._http_client = http_client or httpx.AsyncClient()
        self._aggregator = TickAggregator()
        self._stopping = False
        self._pending_candles: dict[str, list[Candle]] = {}
        self._current_symbols: list[Symbol] = []
        self._candles_flushed = 0

    async def run_forever(self) -> None:
        logger.info("Upstox market feed worker started")
        backoff = self._settings.upstox_ws_reconnect_backoff_seconds
        while not self._stopping:
            try:
                await self._connect_and_stream()
                backoff = self._settings.upstox_ws_reconnect_backoff_seconds
            except ProviderError as exc:
                logger.warning("Upstox market feed: {error}", error=exc)
            except Exception:  # noqa: BLE001 - a bad cycle must never kill this loop
                logger.exception("Upstox market feed: unexpected error")

            if not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._settings.upstox_ws_reconnect_max_backoff_seconds)

        logger.info("Upstox market feed worker stopped")

    def stop(self) -> None:
        self._stopping = True

    # --- connection lifecycle -------------------------------------------

    async def _connect_and_stream(self) -> None:
        symbols = await self._active_symbols()
        if not symbols:
            logger.warning("Upstox market feed: no active symbols to subscribe to yet")
            await asyncio.sleep(self._settings.upstox_ws_reconnect_backoff_seconds)
            return
        self._current_symbols = symbols
        instrument_keys = [s.instrument_token for s in symbols]

        wss_url = await self._authorize()

        connected_at = utc_now()
        feed_log_id = await self._open_feed_log(connected_at)
        messages_received = 0
        disconnect_reason: str | None = None

        try:
            async with websockets.connect(
                wss_url,
                ping_interval=self._settings.upstox_ws_ping_interval_seconds,
                ping_timeout=self._settings.upstox_ws_ping_timeout_seconds,
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "guid": str(uuid.uuid4()),
                            "method": "sub",
                            "data": {
                                "mode": self._settings.upstox_ws_mode,
                                "instrumentKeys": instrument_keys,
                            },
                        }
                    )
                )
                logger.info(
                    "Upstox market feed subscribed to {count} symbols", count=len(instrument_keys)
                )

                # Startup recovery / reconnect gap-fill — see module docstring.
                await self._collector.collect_intraday(symbols=symbols)

                last_flush = time.monotonic()
                while not self._stopping:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=self._settings.upstox_ws_stale_threshold_seconds
                        )
                    except TimeoutError:
                        disconnect_reason = "stale connection (no messages received)"
                        logger.warning(
                            "Upstox market feed: no messages for {s}s, reconnecting",
                            s=self._settings.upstox_ws_stale_threshold_seconds,
                        )
                        break

                    messages_received += 1
                    self._handle_message(raw)

                    if time.monotonic() - last_flush >= self._settings.upstox_ws_flush_interval_seconds:
                        await self._flush()
                        last_flush = time.monotonic()

                if self._stopping:
                    disconnect_reason = "graceful shutdown"
        except websockets.exceptions.ConnectionClosed as exc:
            disconnect_reason = disconnect_reason or f"connection closed: {exc}"
            logger.warning("Upstox market feed disconnected: {error}", error=exc)
        except OSError as exc:
            disconnect_reason = disconnect_reason or f"connection error: {exc}"
            logger.warning("Upstox market feed connection error: {error}", error=exc)
        finally:
            await self._flush()
            await self._close_feed_log(feed_log_id, messages_received, disconnect_reason)

    async def _authorize(self) -> str:
        try:
            response = await self._http_client.get(
                self._settings.upstox_ws_authorize_url,
                headers={
                    "Authorization": f"Bearer {self._settings.upstox_access_token}",
                    "Accept": "application/json",
                },
                timeout=self._settings.upstox_request_timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Upstox market feed authorize failed: {exc}") from exc

        if response.status_code == 401:
            raise ProviderError(
                "Upstox market feed authorize rejected (expired or invalid token)",
                retryable=False,
            )
        if response.status_code != 200:
            raise ProviderError(
                f"Upstox market feed authorize failed: HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("Upstox market feed authorize returned invalid JSON") from exc

        data = body.get("data") if isinstance(body, dict) else None
        url = (data or {}).get("authorized_redirect_uri") if isinstance(data, dict) else None
        if not url or not isinstance(url, str):
            raise ProviderError(
                "Upstox market feed authorize response missing authorized_redirect_uri",
                retryable=False,
            )
        return str(url)

    async def _active_symbols(self) -> list[Symbol]:
        async with self._session_factory() as session:
            return await SymbolRepository(session).list_active()

    # --- message handling ------------------------------------------------

    def _handle_message(self, raw: bytes | str) -> None:
        if isinstance(raw, str):
            logger.warning("Upstox market feed: unexpected text frame, skipping")
            return

        response = pb.FeedResponse()
        try:
            response.ParseFromString(raw)
        except Exception:  # noqa: BLE001 - a malformed frame must never kill the connection
            logger.warning("Upstox market feed: failed to parse frame, skipping")
            return

        for instrument_key, feed in response.feeds.items():
            # We only ever subscribe in ltpc mode, but never assume — a
            # future mode change shouldn't silently misread another
            # message shape as LTPC.
            if feed.WhichOneof("FeedUnion") != "ltpc":
                continue
            ltpc = feed.ltpc
            candle = self._aggregator.ingest(
                instrument_key, ltp=ltpc.ltp, ltt_ms=ltpc.ltt, ltq=ltpc.ltq
            )
            if candle is not None:
                self._pending_candles.setdefault(instrument_key, []).append(candle)

    async def _flush(self) -> None:
        """Batch-writes completed candles and publishes one PipelineEvent —
        never called per-tick, only on the flush interval, shutdown, or a
        disconnect (so a completed-but-unflushed bar is never silently
        dropped)."""
        if not self._pending_candles:
            return
        pending, self._pending_candles = self._pending_candles, {}

        symbol_by_key = {s.instrument_token: s for s in self._current_symbols}
        written = 0
        async with self._session_factory() as session:
            price_repo = PriceRepository(session)
            for instrument_key, candles in pending.items():
                symbol = symbol_by_key.get(instrument_key)
                if symbol is None:
                    continue
                clean = DataValidator.validate_candles(candles, context=symbol.symbol)
                await price_repo.upsert_intraday_many(symbol.id, clean)
                written += len(clean)
            await session.commit()

        if written:
            self._candles_flushed += written
            await self._queue.publish(
                PipelineEvent(source="intraday_ws", symbol_count=len(pending), as_of=utc_now())
            )

    # --- metrics / source tracking ---------------------------------------

    async def _open_feed_log(self, connected_at: datetime) -> int:
        async with self._session_factory() as session:
            log = await MarketDataFeedLogRepository(session).open(connected_at)
            await session.commit()
            return log.id

    async def _close_feed_log(
        self, log_id: int, messages_received: int, disconnect_reason: str | None
    ) -> None:
        async with self._session_factory() as session:
            repo = MarketDataFeedLogRepository(session)
            log = await session.get(MarketDataFeedLog, log_id)
            if log is None:
                return
            # Counters are cumulative for the worker's process lifetime,
            # not reset per connect cycle — simpler than tracking both a
            # running total and a per-cycle delta, and "as of this
            # disconnect, life-of-process totals were X" is still a
            # meaningful metric.
            await repo.close(
                log,
                disconnected_at=utc_now(),
                messages_received=messages_received,
                ticks_processed=self._aggregator.ticks_processed,
                duplicates_dropped=self._aggregator.duplicates_dropped,
                candles_flushed=self._candles_flushed,
                disconnect_reason=disconnect_reason,
            )
            await session.commit()
