"""Tests for app.providers.tick_aggregator.TickAggregator — pure in-memory
tick -> 1-minute-candle aggregation, no I/O."""

from datetime import UTC, datetime
from decimal import Decimal

from app.providers.tick_aggregator import TickAggregator

_KEY = "NSE_EQ|INE467B01029"

# 2026-08-13 09:15:00 UTC and 09:15:30 UTC (same minute), then 09:16:05 UTC
# (next minute) — epoch ms.
_T_09_15_00 = int(datetime(2026, 8, 13, 9, 15, 0, tzinfo=UTC).timestamp() * 1000)
_T_09_15_30 = int(datetime(2026, 8, 13, 9, 15, 30, tzinfo=UTC).timestamp() * 1000)
_T_09_16_05 = int(datetime(2026, 8, 13, 9, 16, 5, tzinfo=UTC).timestamp() * 1000)


def test_first_tick_opens_bucket_and_returns_no_candle() -> None:
    agg = TickAggregator()
    candle = agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_00, ltq=10)

    assert candle is None
    assert agg.ticks_processed == 1


def test_ticks_within_same_minute_update_bucket_without_emitting() -> None:
    agg = TickAggregator()
    agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_00, ltq=10)
    candle = agg.ingest(_KEY, ltp=105.0, ltt_ms=_T_09_15_30, ltq=20)

    assert candle is None
    open_buckets = agg.flush_open_buckets()
    bar = open_buckets[_KEY]
    assert bar.open == Decimal("100.0")
    assert bar.high == Decimal("105.0")
    assert bar.low == Decimal("100.0")
    assert bar.close == Decimal("105.0")
    assert bar.volume == 30


def test_tick_in_new_minute_emits_completed_prior_bucket() -> None:
    agg = TickAggregator()
    agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_00, ltq=10)
    agg.ingest(_KEY, ltp=105.0, ltt_ms=_T_09_15_30, ltq=20)
    completed = agg.ingest(_KEY, ltp=110.0, ltt_ms=_T_09_16_05, ltq=5)

    assert completed is not None
    assert completed.open == Decimal("100.0")
    assert completed.high == Decimal("105.0")
    assert completed.close == Decimal("105.0")
    assert completed.volume == 30
    assert completed.timestamp == datetime(2026, 8, 13, 9, 15, 0, tzinfo=UTC)

    # A new bucket opened for the just-ingested tick.
    open_buckets = agg.flush_open_buckets()
    assert open_buckets[_KEY].open == Decimal("110.0")
    assert open_buckets[_KEY].volume == 5


def test_duplicate_or_out_of_order_tick_is_dropped() -> None:
    agg = TickAggregator()
    agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_30, ltq=10)

    # Same ltt again (duplicate) and an earlier ltt (out of order) both drop.
    dup = agg.ingest(_KEY, ltp=999.0, ltt_ms=_T_09_15_30, ltq=999)
    stale = agg.ingest(_KEY, ltp=999.0, ltt_ms=_T_09_15_00, ltq=999)

    assert dup is None
    assert stale is None
    assert agg.duplicates_dropped == 2
    assert agg.ticks_processed == 1  # only the first tick counted

    # The bucket is untouched by the dropped ticks.
    bar = agg.flush_open_buckets()[_KEY]
    assert bar.close == Decimal("100.0")
    assert bar.volume == 10


def test_decimal_precision_avoids_binary_float_artifacts() -> None:
    agg = TickAggregator()
    agg.ingest(_KEY, ltp=19.95, ltt_ms=_T_09_15_00, ltq=1)

    bar = agg.flush_open_buckets()[_KEY]
    assert bar.open == Decimal("19.95")
    assert str(bar.open) == "19.95"  # not "19.949999999999999...")


def test_instruments_tracked_independently() -> None:
    agg = TickAggregator()
    other_key = "NSE_EQ|INE009A01021"

    agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_00, ltq=10)
    agg.ingest(other_key, ltp=200.0, ltt_ms=_T_09_15_00, ltq=5)

    open_buckets = agg.flush_open_buckets()
    assert open_buckets[_KEY].open == Decimal("100.0")
    assert open_buckets[other_key].open == Decimal("200.0")


def test_flush_open_buckets_does_not_clear_state() -> None:
    agg = TickAggregator()
    agg.ingest(_KEY, ltp=100.0, ltt_ms=_T_09_15_00, ltq=10)

    first = agg.flush_open_buckets()
    second = agg.flush_open_buckets()

    assert first[_KEY].open == second[_KEY].open == Decimal("100.0")
