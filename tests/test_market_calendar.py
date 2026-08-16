"""Tests for app.market_calendar, using actual verified 2026 NSE dates
(NSE circular NSE/FAOP/71777 for the F&O-confirmed dates; see
app/market_calendar/data/year_2026.py for full provenance, including the
one equity-only date whose provenance is weaker by design)."""

from datetime import UTC, datetime

import pytest

from app.core.time import market_date_of
from app.market_calendar import (
    CalendarYearNotVerifiedError,
    MarketSegment,
    get_holiday,
    is_market_holiday,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
)

_EQ = MarketSegment.EQUITY
_FNO = MarketSegment.FNO


def test_normal_trading_day() -> None:
    tuesday = datetime(2026, 1, 27).date()  # confirmed Tuesday, no holiday
    assert is_trading_day(tuesday, _EQ) is True
    assert is_trading_day(tuesday, _FNO) is True


def test_saturday_is_not_a_trading_day() -> None:
    saturday = datetime(2026, 1, 10).date()
    assert is_trading_day(saturday, _EQ) is False
    assert is_trading_day(saturday, _FNO) is False


def test_sunday_is_not_a_trading_day() -> None:
    sunday = datetime(2026, 1, 25).date()
    assert is_trading_day(sunday, _EQ) is False
    assert is_trading_day(sunday, _FNO) is False


def test_verified_nse_holiday_republic_day() -> None:
    """2026-01-26, Monday — Republic Day, confirmed by the F&O circular
    and shared across both segments."""
    republic_day = datetime(2026, 1, 26).date()
    assert is_market_holiday(republic_day, _EQ) is True
    assert is_market_holiday(republic_day, _FNO) is True
    assert is_trading_day(republic_day, _EQ) is False
    assert is_trading_day(republic_day, _FNO) is False
    holiday = get_holiday(republic_day, _EQ)
    assert holiday is not None
    assert holiday.name == "Republic Day"
    assert holiday.verified_via_official_circular is True


def test_equity_only_holiday_differs_from_fno() -> None:
    """The one genuine segment-specific case in the 2026 data: 2026-01-15
    is an Equity-segment closure absent from the F&O circular."""
    jan_15 = datetime(2026, 1, 15).date()
    assert is_trading_day(jan_15, _EQ) is False
    assert is_trading_day(jan_15, _FNO) is True
    holiday = get_holiday(jan_15, _EQ)
    assert holiday is not None
    assert holiday.verified_via_official_circular is False  # weaker provenance, by design
    assert get_holiday(jan_15, _FNO) is None


def test_day_before_holiday_is_a_trading_day() -> None:
    # 2026-11-10 (Tuesday) is Diwali-Balipratipada; 2026-11-09 (Monday) is not a holiday.
    assert is_trading_day(datetime(2026, 11, 9).date(), _EQ) is True


def test_day_after_holiday_is_a_trading_day() -> None:
    assert is_trading_day(datetime(2026, 11, 11).date(), _EQ) is True


def test_previous_trading_day_around_a_midweek_holiday() -> None:
    diwali = datetime(2026, 11, 10).date()
    assert previous_trading_day(diwali, _EQ) == datetime(2026, 11, 9).date()


def test_next_trading_day_around_a_midweek_holiday() -> None:
    diwali = datetime(2026, 11, 10).date()
    assert next_trading_day(diwali, _EQ) == datetime(2026, 11, 11).date()


def test_previous_trading_day_skips_weekend_and_holiday_together() -> None:
    # 2026-01-27 (Tue, trading) <- 2026-01-26 (Mon, Republic Day) <- 2026-01-25/24 (weekend)
    # -> previous trading day before the 27th is Friday 2026-01-23.
    assert previous_trading_day(datetime(2026, 1, 27).date(), _EQ) == datetime(2026, 1, 23).date()


def test_next_trading_day_skips_weekend() -> None:
    friday = datetime(2026, 8, 14).date()
    assert next_trading_day(friday, _EQ) == datetime(2026, 8, 17).date()  # Monday


def test_freshness_across_weekend_friday_to_monday() -> None:
    """The literal example from the spec: Friday last trading day, Monday
    next — the weekend must not look like a gap."""
    friday = datetime(2026, 8, 14).date()
    monday = datetime(2026, 8, 17).date()
    assert is_trading_day(friday, _EQ) is True
    assert is_trading_day(datetime(2026, 8, 15).date(), _EQ) is False  # Saturday
    assert is_trading_day(datetime(2026, 8, 16).date(), _EQ) is False  # Sunday
    assert is_trading_day(monday, _EQ) is True
    assert next_trading_day(friday, _EQ) == monday


def test_freshness_across_nse_holiday_monday_to_tuesday() -> None:
    """If Monday is an official NSE holiday and Tuesday is the next
    trading day, Monday must not be treated as a missing/stale day."""
    monday_holiday = datetime(2026, 1, 26).date()
    tuesday = datetime(2026, 1, 27).date()
    assert is_trading_day(monday_holiday, _EQ) is False
    assert next_trading_day(datetime(2026, 1, 23).date(), _EQ) == tuesday


def test_special_session_diwali_laxmi_pujan_is_recorded_not_silently_dropped() -> None:
    holiday = get_holiday(datetime(2026, 11, 8).date(), _EQ)
    assert holiday is not None
    assert holiday.special_session is True
    assert holiday.special_session_note is not None
    assert "Muhurat" in holiday.special_session_note


def test_ist_date_boundary_resolves_through_the_calendar() -> None:
    """2026-01-25 23:00 UTC is 2026-01-26 04:30 IST — the business date
    the calendar must be asked about is the 26th (Republic Day), not the
    UTC-side 25th (a plain Sunday, also non-trading, but for the wrong
    reason if this boundary were handled incorrectly)."""
    utc_instant = datetime(2026, 1, 25, 23, 0, tzinfo=UTC)
    ist_date = market_date_of(utc_instant, "Asia/Kolkata")
    assert ist_date == datetime(2026, 1, 26).date()
    assert is_trading_day(ist_date, _EQ) is False
    holiday = get_holiday(ist_date, _EQ)
    assert holiday is not None and holiday.name == "Republic Day"


def test_unverified_year_raises_instead_of_guessing() -> None:
    unverified = datetime(2025, 12, 31).date()
    with pytest.raises(CalendarYearNotVerifiedError):
        is_trading_day(unverified, _EQ)
    with pytest.raises(CalendarYearNotVerifiedError):
        is_market_holiday(unverified, _EQ)
    with pytest.raises(CalendarYearNotVerifiedError):
        get_holiday(unverified, _EQ)


def test_walking_into_an_unverified_year_raises() -> None:
    # 2026-01-01 is a Thursday with no holiday before it inside 2026 —
    # walking backward crosses into unverified 2025.
    with pytest.raises(CalendarYearNotVerifiedError):
        previous_trading_day(datetime(2026, 1, 1).date(), _EQ)
