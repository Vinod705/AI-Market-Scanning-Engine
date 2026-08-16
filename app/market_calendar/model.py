"""The verified-holiday data model. Kept separate from `holidays.py` so
per-year data modules (`app/market_calendar/data/year_YYYY.py`) can build
`NSEHoliday` instances without importing the year-registry module that,
in turn, imports them — avoids a circular import."""

from dataclasses import dataclass
from datetime import date

from app.market_calendar.segments import MarketSegment


@dataclass(frozen=True)
class NSEHoliday:
    """One verified NSE calendar entry.

    `verified_via_official_circular` distinguishes a date confirmed by
    an actual NSE circular document (has a circular reference number,
    department, signatory) from one confirmed only by the project
    owner's own attestation without an independently-verifiable
    circular — see `app/market_calendar/data/year_2026.py`'s module
    docstring for the one real instance of the latter this project has.
    """

    date: date
    name: str
    segments: frozenset[MarketSegment]
    source: str
    verified_via_official_circular: bool
    special_session: bool = False
    special_session_note: str | None = None

    @property
    def year(self) -> int:
        return self.date.year
