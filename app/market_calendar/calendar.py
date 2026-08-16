"""Central, segment-aware NSE trading-calendar logic.

Deliberately pure: every function here takes a plain `date` and never
touches `Settings` or a timezone — the IST business-date conversion
boundary already lives in `app.core.time` (see that module's docstring)
and this module doesn't duplicate it. Callers pass an already-resolved
IST/market business date (typically `app.core.time.market_today()` or
`market_date_of()`).

Never silently falls back to a generic or guessed holiday calendar for a
year this module doesn't have verified data for — `is_trading_day()`,
`is_market_holiday()`, and `get_holiday()` all raise
`CalendarYearNotVerifiedError` in that case, and `previous_trading_day()`/
`next_trading_day()` propagate the same error the moment their walk
crosses into an unverified year. Callers (e.g. the sanity system) are
expected to catch this and report it plainly, per this feature's own
safety rule — see `app.sanity.checks.check_market_calendar`.
"""

from datetime import date, timedelta

from app.market_calendar.holidays import HOLIDAYS_BY_YEAR, NSEHoliday
from app.market_calendar.segments import MarketSegment

# previous/next_trading_day walk bounded to this many days — real NSE
# holiday clusters (e.g. around Diwali) never run anywhere close to this
# many consecutive non-trading days; a walk exceeding it means a genuine
# bug, not a slow calendar.
_MAX_WALK_DAYS = 30


class CalendarYearNotVerifiedError(ValueError):
    """Raised instead of guessing when asked about a date in a year this
    module has no verified NSE holiday data for."""

    def __init__(self, year: int, segment: MarketSegment) -> None:
        self.year = year
        self.segment = segment
        super().__init__(
            f"No verified NSE {segment.value} holiday calendar for {year} — "
            f"verified years are {sorted(HOLIDAYS_BY_YEAR)}. Not guessing."
        )


def verified_years() -> frozenset[int]:
    return frozenset(HOLIDAYS_BY_YEAR)


def _require_verified(year: int, segment: MarketSegment) -> None:
    if year not in HOLIDAYS_BY_YEAR:
        raise CalendarYearNotVerifiedError(year, segment)


def get_holiday(day: date, segment: MarketSegment) -> NSEHoliday | None:
    """The verified holiday record for `day` applying to `segment`, or
    `None` if `day` isn't a holiday for that segment. Raises
    `CalendarYearNotVerifiedError` for an unverified year rather than
    silently reporting "not a holiday", which would be indistinguishable
    from a genuinely-checked, genuinely-clean day."""
    _require_verified(day.year, segment)
    for holiday in HOLIDAYS_BY_YEAR[day.year]:
        if holiday.date == day and segment in holiday.segments:
            return holiday
    return None


def is_market_holiday(day: date, segment: MarketSegment) -> bool:
    """Whether NSE has flagged `day` as a holiday for `segment` —
    independent of whether `day` is also a weekend (see
    `is_trading_day()` for the combined answer)."""
    return get_holiday(day, segment) is not None


def is_trading_day(day: date, segment: MarketSegment) -> bool:
    """Whether `segment` actually trades on `day`: a weekday that isn't
    an NSE holiday for that segment. Equity and F&O can genuinely
    disagree — see `app/market_calendar/data/year_2026.py`."""
    if day.weekday() >= 5:  # Saturday, Sunday
        return False
    return not is_market_holiday(day, segment)


def previous_trading_day(day: date, segment: MarketSegment) -> date:
    """The most recent trading day strictly before `day`."""
    cursor = day
    for _ in range(_MAX_WALK_DAYS):
        cursor = cursor - timedelta(days=1)
        if is_trading_day(cursor, segment):
            return cursor
    raise RuntimeError(
        f"previous_trading_day({day}, {segment}) found no trading day within "
        f"{_MAX_WALK_DAYS} days — likely a data bug, not a real NSE gap"
    )


def next_trading_day(day: date, segment: MarketSegment) -> date:
    """The next trading day strictly after `day`."""
    cursor = day
    for _ in range(_MAX_WALK_DAYS):
        cursor = cursor + timedelta(days=1)
        if is_trading_day(cursor, segment):
            return cursor
    raise RuntimeError(
        f"next_trading_day({day}, {segment}) found no trading day within "
        f"{_MAX_WALK_DAYS} days — likely a data bug, not a real NSE gap"
    )
