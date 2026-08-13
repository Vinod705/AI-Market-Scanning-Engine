"""Shared UTC/market-timezone datetime helpers.

Convention: every internal or persisted timestamp (anything compared
against or stored into a `DateTime(timezone=True)` column) is timezone-aware
UTC. `utc_now()` is the only sanctioned way to get "now" in that context —
never call bare `datetime.now()`, which returns a naive value in whatever
timezone the host OS happens to be set to and silently produces wrong
comparisons against aware DB columns on any host that isn't UTC.

`to_market_time()` is the one conversion boundary: it turns an aware (or
UTC-assumed-naive) instant into the configured market timezone (IST by
default, via `Settings.market_timezone`) for display or trading-calendar
purposes — e.g. formatting a Telegram alert's send time, or deciding which
trading day "today" is. Everywhere else should stay in UTC.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """Timezone-aware current instant in UTC."""
    return datetime.now(UTC)


def to_market_time(moment: datetime, market_timezone: str) -> datetime:
    """Convert `moment` to the given market timezone. A naive `moment` is
    assumed to already be UTC (matching this codebase's convention) rather
    than the host's local time."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(market_timezone))
