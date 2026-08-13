"""TickAggregator: pure, in-memory tick -> 1-minute-candle aggregation.

No I/O, no wall-clock timers — purely reactive to whatever ticks are fed to
it via `ingest()`. Kept this way so both the OHLCV bucket logic and the
"duplicate-event protection" (dedup by last-trade-time) are trivially
unit-testable without a real WebSocket connection anywhere nearby. Emits
`app.providers.base_provider.Candle` — the exact type
`PriceRepository.upsert_intraday_many` already accepts, so a caller can hand
completed bars straight to the same repository method the REST ingestion
path already uses.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.providers.base_provider import Candle


@dataclass
class _OpenBucket:
    minute: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class TickAggregator:
    def __init__(self) -> None:
        self._buckets: dict[str, _OpenBucket] = {}
        self._last_ltt_ms: dict[str, int] = {}
        self.duplicates_dropped = 0
        self.ticks_processed = 0

    def ingest(self, instrument_key: str, *, ltp: float, ltt_ms: int, ltq: int) -> Candle | None:
        """Feed one LTPC tick (last traded price/time/quantity). Returns a
        completed `Candle` if this tick rolled the instrument's bucket over
        into a new minute, else `None`.

        Duplicate/out-of-order ticks (`ltt_ms` at or before the last one
        seen for this instrument) are dropped — counted via
        `duplicates_dropped`, never raised. Upstox trade time is monotonic
        per instrument within one live stream, so this alone is a real
        duplicate guard; it does not, and cannot, recover ticks missed
        entirely during a disconnect — that's handled by a REST backfill
        on reconnect, not here (see `app.providers.upstox_websocket`).
        """
        last_ltt = self._last_ltt_ms.get(instrument_key)
        if last_ltt is not None and ltt_ms <= last_ltt:
            self.duplicates_dropped += 1
            return None
        self._last_ltt_ms[instrument_key] = ltt_ms
        self.ticks_processed += 1

        # Deliberately Decimal(str(...)), never Decimal(ltp) directly — a
        # double cast straight to Decimal bakes in binary-float artifacts
        # (e.g. Decimal(19.95) != Decimal("19.95")).
        price = Decimal(str(ltp))
        minute = datetime.fromtimestamp(ltt_ms / 1000, tz=UTC).replace(second=0, microsecond=0)

        bucket = self._buckets.get(instrument_key)
        completed: Candle | None = None
        if bucket is not None and bucket.minute != minute:
            completed = _to_candle(bucket)
            bucket = None

        if bucket is None:
            bucket = _OpenBucket(
                minute=minute, open=price, high=price, low=price, close=price, volume=int(ltq)
            )
        else:
            bucket.high = max(bucket.high, price)
            bucket.low = min(bucket.low, price)
            bucket.close = price
            bucket.volume += int(ltq)

        self._buckets[instrument_key] = bucket
        return completed

    def flush_open_buckets(self) -> dict[str, Candle]:
        """Snapshot every instrument's currently-open (not-yet-closed)
        bucket as a `Candle`, without clearing dedup/bucket state — used on
        graceful shutdown so an in-progress bar isn't silently dropped."""
        return {key: _to_candle(bucket) for key, bucket in self._buckets.items()}


def _to_candle(bucket: _OpenBucket) -> Candle:
    return Candle(
        timestamp=bucket.minute,
        open=bucket.open,
        high=bucket.high,
        low=bucket.low,
        close=bucket.close,
        volume=bucket.volume,
    )
