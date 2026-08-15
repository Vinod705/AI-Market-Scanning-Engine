"""Tests for app.derivatives.futures_oi.reading_from_futures_bars."""

from datetime import date, datetime
from decimal import Decimal

from app.derivatives.derivatives_models import BuildupClassification, InstrumentType
from app.derivatives.futures_oi import reading_from_futures_bars
from app.providers.base_provider import FuturesOiBar


def _bar(timestamp: datetime, close: Decimal, oi: int) -> FuturesOiBar:
    return FuturesOiBar(
        instrument_key="NSE_FO|68797",
        expiry_date=date(2026, 9, 29),
        timestamp=timestamp,
        close=close,
        volume=100_000,
        open_interest=oi,
    )


def test_no_bars_returns_none() -> None:
    assert reading_from_futures_bars("TCS", []) is None


def test_single_bar_has_no_prior_reading_and_is_neutral() -> None:
    bars = [_bar(datetime(2026, 8, 14), Decimal("2400"), 1_000_000)]
    reading = reading_from_futures_bars("TCS", bars)
    assert reading is not None
    assert reading.prev_price is None
    assert reading.prev_oi is None
    assert reading.classification == BuildupClassification.NEUTRAL


def test_two_bars_price_up_oi_up_is_long_buildup() -> None:
    bars = [
        _bar(datetime(2026, 8, 13), Decimal("2350"), 1_000_000),
        _bar(datetime(2026, 8, 14), Decimal("2400"), 1_100_000),
    ]
    reading = reading_from_futures_bars("TCS", bars)
    assert reading is not None
    assert reading.instrument_type == InstrumentType.FUTURES
    assert reading.strike_price is None
    assert reading.oi_change == Decimal("100000")
    assert reading.classification == BuildupClassification.LONG_BUILDUP


def test_two_bars_price_down_oi_down_is_long_unwinding() -> None:
    bars = [
        _bar(datetime(2026, 8, 13), Decimal("2400"), 1_100_000),
        _bar(datetime(2026, 8, 14), Decimal("2350"), 1_000_000),
    ]
    reading = reading_from_futures_bars("TCS", bars)
    assert reading is not None
    assert reading.classification == BuildupClassification.LONG_UNWINDING


def test_uses_latest_of_more_than_two_bars() -> None:
    bars = [
        _bar(datetime(2026, 8, 10), Decimal("2300"), 900_000),
        _bar(datetime(2026, 8, 13), Decimal("2350"), 1_000_000),
        _bar(datetime(2026, 8, 14), Decimal("2400"), 1_100_000),
    ]
    reading = reading_from_futures_bars("TCS", bars)
    assert reading is not None
    assert reading.price == Decimal("2400")
    assert reading.prev_price == Decimal("2350")
