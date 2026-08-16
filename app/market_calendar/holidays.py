"""The year-keyed holiday registry every `app.market_calendar.calendar`
function reads from.

Adding a new year means adding one `app/market_calendar/data/year_YYYY.py`
module (same shape as `year_2026.py`) and registering it in
`HOLIDAYS_BY_YEAR` below — never editing dates in place elsewhere.
"""

from app.market_calendar.data.year_2026 import HOLIDAYS_2026
from app.market_calendar.model import NSEHoliday

__all__ = ["HOLIDAYS_BY_YEAR", "NSEHoliday"]

HOLIDAYS_BY_YEAR: dict[int, tuple[NSEHoliday, ...]] = {
    2026: HOLIDAYS_2026,
}
