"""Shared UTC/market-timezone datetime helpers.

Convention: every internal or persisted TECHNICAL timestamp (anything
compared against or stored into a `DateTime(timezone=True)` column —
`created_at`, `requested_at`, a worker heartbeat, an alert's own instant)
is timezone-aware UTC. `utc_now()` is the only sanctioned way to get "now"
in that context — never call bare `datetime.now()`, which returns a naive
value in whatever timezone the host OS happens to be set to and silently
produces wrong comparisons against aware DB columns on any host that
isn't UTC.

`to_market_time()` is the one conversion boundary: it turns an aware (or
UTC-assumed-naive) instant into the configured market timezone (IST by
default, via `Settings.market_timezone`) for display or trading-calendar
purposes.

**Indian market/BUSINESS dates are a separate concern from the technical
timestamp above.** Whenever code answers "what trading day is this" —
`scanner_results.date`, `Alert.signal_date`, a daily request-budget's
"today", `daily_features`/`daily_prices` freshness, the IPO-universe age
cutoff — that calendar date MUST be derived in `Settings.market_timezone`
(IST), never in UTC and never in the host's local timezone. `now_market_time()`
and `market_today()` below are the one, single way to do that; nothing
else in the codebase should call `date.today()`, bare `datetime.now()`,
or `datetime.now(UTC).date()` for a business-date decision. A technical
UTC instant (`utc_now()`) is exactly correct for row-creation timestamps,
heartbeats, and elapsed-time math — the point is not "replace every UTC
timestamp with IST", only "every Indian-market business-date extraction
must go through IST explicitly".
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """Timezone-aware current instant in UTC. The right choice for a
    TECHNICAL timestamp (row creation time, a heartbeat, "how many
    seconds since X") — never for an Indian market business-date
    decision; see `market_today()` for that."""
    return datetime.now(UTC)


def to_market_time(moment: datetime, market_timezone: str) -> datetime:
    """Convert `moment` to the given market timezone. A naive `moment` is
    assumed to already be UTC (matching this codebase's convention) rather
    than the host's local time."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(market_timezone))


def now_market_time(market_timezone: str | None = None) -> datetime:
    """The current instant, expressed in the configured Indian market
    timezone (IST by default) — for BUSINESS-date derivations. Reads
    `Settings.market_timezone` when `market_timezone` isn't supplied
    explicitly (e.g. from a dataclass `default_factory`, which can't take
    a settings object), so this stays the one central place the business
    timezone is configured, per this module's own docstring."""
    tz = market_timezone or _default_market_timezone()
    return to_market_time(utc_now(), tz)


def market_today(market_timezone: str | None = None) -> date:
    """Today's calendar date in the configured Indian market timezone
    (IST by default) — the one function every "what is today's Indian
    trading/business date" call site should use instead of
    `date.today()` or `datetime.now(UTC).date()`. See this module's
    docstring for why those two are each wrong for this purpose in a
    different way (host-local vs. UTC-not-IST)."""
    return now_market_time(market_timezone).date()


def market_date_of(moment: datetime, market_timezone: str | None = None) -> date:
    """The Indian trading date `moment` falls on — the one function every
    "which trading day does this instant belong to" call site should
    use, instead of a bare `.date()` (which silently depends on whatever
    tzinfo the caller happens to be carrying: correct for a genuinely
    IST-offset-aware timestamp, wrong for a UTC-aware or naive one — a
    real ambiguity this project hit in its own test suite, not a
    hypothetical). Falls back to `Settings.market_timezone` the same way
    `now_market_time()` does."""
    tz = market_timezone or _default_market_timezone()
    return to_market_time(moment, tz).date()


def market_day_bounds_utc(
    day: date, market_timezone: str | None = None
) -> tuple[datetime, datetime]:
    """The `[start, end)` instant bounds, in UTC, of one Indian trading
    day. This is the correct way to filter a `DateTime(timezone=True)`
    UTC-stored column (e.g. `requested_at`) by an IST business date —
    `func.date(some_utc_column)` at the SQL level truncates in the
    database's own session timezone (UTC), not IST, so comparing that
    against a Python-computed IST date silently mismatches near
    midnight. A range comparison against these bounds sidesteps the
    ambiguity entirely and works identically on Postgres and SQLite."""
    tz = market_timezone or _default_market_timezone()
    zone = ZoneInfo(tz)
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _default_market_timezone() -> str:
    from app.config.settings import get_settings

    return get_settings().market_timezone
