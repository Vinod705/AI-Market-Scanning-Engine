"""Domain type for the cached-snapshot read path — kept separate from
`app.fundamentals.models` (raw provider data) and `app.fundamentals.queue_models`
(the fetch process) since this describes the *persisted cache row*, the
"cached snapshot" stage of Fundamental provider -> background sync ->
PostgreSQL -> cached snapshot -> intraday scanner.
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.fundamentals.models import FundamentalData
from app.fundamentals.queue_models import FetchStatus


@dataclass
class CachedFundamentalSnapshot:
    """What `FundamentalSnapshotRepository.get_cached()` returns — a pure
    DB read, never a live provider call (see that method's docstring for
    the non-blocking guarantee this exists to provide)."""

    symbol_id: int
    data: FundamentalData | None
    source: str | None
    as_of: date | None
    fetched_at: datetime | None
    last_checked_at: datetime
    status: FetchStatus
    error_message: str | None
    is_fresh: bool
