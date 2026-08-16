"""Central, verified NSE trading-calendar module.

See `app.market_calendar.calendar`'s module docstring for the full
design: segment-aware (Equity vs F&O genuinely differ in 2026), backed
only by verified-source holiday data, never falls back to a guessed or
generic calendar.
"""

from app.market_calendar.calendar import (
    CalendarYearNotVerifiedError,
    get_holiday,
    is_market_holiday,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    verified_years,
)
from app.market_calendar.holidays import NSEHoliday
from app.market_calendar.segments import MarketSegment

__all__ = [
    "CalendarYearNotVerifiedError",
    "MarketSegment",
    "NSEHoliday",
    "get_holiday",
    "is_market_holiday",
    "is_trading_day",
    "next_trading_day",
    "previous_trading_day",
    "verified_years",
]
