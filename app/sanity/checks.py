"""Individual, read-only sanity checks. Every function here only ever
reads — no market data, feature, scanner, or configuration write ever
happens in this module (the one write anywhere in `app.sanity`'s
neighborhood is the worker heartbeat ping, which lives in the workers
themselves, not here — see `app.repositories.worker_heartbeat_repository`).
"""

from datetime import datetime as datetime_

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config.settings import Settings
from app.core.time import market_date_of, utc_now
from app.health.freshness import FreshnessStatus, check_feature_freshness
from app.market_calendar import (
    CalendarYearNotVerifiedError,
    MarketSegment,
    get_holiday,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    verified_years,
)
from app.repositories.market_repository import MarketDataFeedLogRepository, PriceRepository
from app.repositories.scanner_repository import ScannerResultRepository, ScannerRunRepository
from app.repositories.worker_heartbeat_repository import WorkerHeartbeatRepository
from app.sanity.models import ComponentCheck, SanityStatus, ScannerCheck

_EXPECTED_MARKET_TIMEZONE = "Asia/Kolkata"
# Convention a worker's heartbeat `detail` uses to mark "I ran, correctly
# determined there was nothing to do, this is not a failure" — see
# check_worker_heartbeat below. Currently only app.scheduler.momentum_pipeline_jobs
# uses this (market-closed skip).
_BENIGN_SKIP_PREFIX = "skipped:"

# The 7 real scanners, grouped by universe — see app/scanner/*.py and
# app/candidates/*_scanner.py. Not read from ScannerRegistry to keep this
# module import-light and independent of the live app's DI wiring (the
# sanity checker must be runnable standalone, e.g. via the CLI, without
# constructing the whole app).
LISTED_SCANNERS = ("breakout_v1", "vcp_v1", "momentum_v1", "orb_v1")
CANDIDATE_SCANNERS = ("fno_momentum_v1", "pre_breakout_v1", "ipo_intraday_v1")
ALL_SCANNERS = LISTED_SCANNERS + CANDIDATE_SCANNERS

# Worker heartbeat names — must match the WORKER_NAME/*_WORKER_NAME
# constants each worker pings under (app.pipeline.worker,
# app.features.engine, app.scanner.engine, app.scheduler.momentum_pipeline_jobs,
# app.alerts.manager).
EVENT_DRIVEN_WORKERS = ("pipeline_worker", "feature_engine", "scanner_engine", "alert_manager")
MOMENTUM_PIPELINE_WORKER = "momentum_pipeline"


def check_timezone_configuration(settings: Settings) -> ComponentCheck:
    """Confirms the application's authoritative business timezone is
    actually `Asia/Kolkata` — every "today"/scanner-date/daily-budget/
    freshness calculation in this codebase derives from
    `Settings.market_timezone` (see `app.core.time`'s module docstring),
    so a misconfigured value here would silently make every one of those
    business-date decisions wrong. Pure config check, no I/O."""
    configured = settings.market_timezone
    if configured != _EXPECTED_MARKET_TIMEZONE:
        return ComponentCheck(
            "Application Timezone",
            SanityStatus.WARNING,
            f"market_timezone={configured!r}, expected {_EXPECTED_MARKET_TIMEZONE!r} — "
            "every Indian market business-date calculation in this codebase depends on this "
            "being correct",
        )
    return ComponentCheck(
        "Application Timezone", SanityStatus.HEALTHY, f"market_timezone={configured!r}"
    )


def check_market_calendar(
    settings: Settings, *, now: datetime_ | None = None
) -> ComponentCheck:
    """Reports the verified NSE trading calendar's view of "today", per
    the Equity/Capital Market segment (the segment `daily_prices`,
    `daily_features`, and the LISTED/IPO-universe scanners all key off —
    see `app.market_calendar.segments.MarketSegment`'s docstring).

    Never silently falls back to a generic calendar: if the current
    year has no verified NSE holiday data, this reports NOT_CONFIGURED
    with that fact stated plainly, per this feature's own safety rule."""
    now = now or utc_now()
    today = market_date_of(now, settings.market_timezone)
    segment = MarketSegment.EQUITY

    try:
        equity_trading = is_trading_day(today, segment)
        fno_trading = is_trading_day(today, MarketSegment.FNO)
        holiday = get_holiday(today, segment)
        upcoming = next_trading_day(today, segment)
        prior = previous_trading_day(today, segment)
    except CalendarYearNotVerifiedError as exc:
        return ComponentCheck("Market Calendar", SanityStatus.NOT_CONFIGURED, str(exc))

    if equity_trading:
        today_status = "Trading Day"
    elif today.weekday() >= 5:
        today_status = f"Weekend (also NSE Holiday: {holiday.name})" if holiday else "Weekend"
    else:
        today_status = f"NSE Holiday: {holiday.name}" if holiday else "Weekend"

    segment_note = "" if equity_trading == fno_trading else (
        f" — NOTE: F&O segment {'trades' if fno_trading else 'does not trade'} today, "
        "differs from Equity"
    )

    detail = (
        f"Timezone: {settings.market_timezone} | Current Market Date: {today} | "
        f"Today: {today_status} | Next Trading Day: {upcoming} | "
        f"Previous Trading Day: {prior} | Source: NSE India | Calendar: "
        f"{segment.value} | Year: {today.year} | Verified years: "
        f"{sorted(verified_years())}{segment_note}"
    )
    return ComponentCheck("Market Calendar", SanityStatus.HEALTHY, detail)


async def check_database(engine: AsyncEngine) -> ComponentCheck:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return ComponentCheck("PostgreSQL", SanityStatus.HEALTHY, "reachable")
    except Exception as exc:  # noqa: BLE001 - a sanity check must never itself raise
        return ComponentCheck("PostgreSQL", SanityStatus.FAILED, f"unreachable: {exc}")


async def check_redis(redis_client: Redis, settings: Settings) -> ComponentCheck:
    try:
        pong = await redis_client.ping()
        if not pong:
            return ComponentCheck("Redis", SanityStatus.FAILED, "PING returned falsy")
    except Exception as exc:  # noqa: BLE001 - a sanity check must never itself raise
        return ComponentCheck("Redis", SanityStatus.FAILED, f"unreachable: {exc}")

    lag_detail = await _check_stream_lag(redis_client, settings)
    return ComponentCheck("Redis", SanityStatus.HEALTHY, f"reachable; {lag_detail}")


async def _check_stream_lag(redis_client: Redis, settings: Settings) -> str:
    """Best-effort — a missing stream/group (e.g. nothing published yet on
    a fresh deployment) is reported informationally, never as a failure
    of Redis itself."""
    try:
        groups = await redis_client.xinfo_groups(settings.pipeline_stream_key)
    except Exception:  # noqa: BLE001 - stream/group may simply not exist yet
        return "no consumer group info available yet"
    for group in groups:
        if group.get("name") == settings.pipeline_consumer_group:
            pending = group.get("pending", 0)
            lag = group.get("lag")
            return f"consumer group '{settings.pipeline_consumer_group}' pending={pending} lag={lag}"
    return f"consumer group '{settings.pipeline_consumer_group}' not found"


async def check_data_freshness(
    session: AsyncSession, settings: Settings
) -> tuple[ComponentCheck, ComponentCheck, FreshnessStatus]:
    """Reuses `app.health.freshness.check_feature_freshness` rather than
    recomputing the daily_prices-vs-daily_features comparison — this is
    the exact check that motivated this whole system (see that module's
    docstring for the real incident). Returns the raw `FreshnessStatus`
    too, so callers (the sanity report) get real `date` objects instead
    of re-parsing them out of a formatted detail string."""
    freshness = await check_feature_freshness(session, settings)

    if freshness.daily_prices_date is None:
        prices_check = ComponentCheck(
            "daily_prices", SanityStatus.UNKNOWN, "no price data yet (fresh database?)"
        )
        features_check = ComponentCheck(
            "daily_features", SanityStatus.UNKNOWN, "no price data to compare against yet"
        )
        return prices_check, features_check, freshness

    prices_check = ComponentCheck(
        "daily_prices",
        SanityStatus.HEALTHY,
        f"latest date {freshness.daily_prices_date}",
    )

    if freshness.daily_features_date is None:
        features_check = ComponentCheck(
            "daily_features", SanityStatus.FAILED, "no daily_features computed for any active symbol"
        )
    elif freshness.is_stale:
        features_check = ComponentCheck(
            "daily_features",
            SanityStatus.STALE,
            f"latest date {freshness.daily_features_date}, {freshness.lag_days} day(s) "
            f"behind daily_prices ({freshness.daily_prices_date})",
        )
    else:
        features_check = ComponentCheck(
            "daily_features", SanityStatus.HEALTHY, f"latest date {freshness.daily_features_date}"
        )
    return prices_check, features_check, freshness


async def check_market_data_pipeline(
    session: AsyncSession, settings: Settings, *, is_market_open: bool
) -> ComponentCheck:
    """Detects "ingestion connected but nothing is actually landing" —
    distinct from the daily_prices/daily_features check above, which
    only judges the *relationship* between two tables, not whether
    either is moving in real time right now."""
    latest_intraday = await PriceRepository(session).get_latest_intraday_timestamp_across_active_symbols()
    recent_feed_logs = await MarketDataFeedLogRepository(session).list_recent(limit=1)
    feed_connected = bool(recent_feed_logs) and recent_feed_logs[0].disconnected_at is None

    if not is_market_open:
        detail = "market closed — no live ticks expected"
        return ComponentCheck("Market Data", SanityStatus.NOT_CONFIGURED, detail)

    if latest_intraday is None:
        return ComponentCheck(
            "Market Data", SanityStatus.FAILED, "no intraday ticks recorded for any active symbol"
        )

    age_seconds = (utc_now() - _as_aware(latest_intraday)).total_seconds()
    stale_threshold = settings.sanity_worker_heartbeat_warning_seconds
    if age_seconds > stale_threshold:
        return ComponentCheck(
            "Market Data",
            SanityStatus.STALE,
            f"last tick {int(age_seconds)}s ago (feed connected={feed_connected})",
        )
    return ComponentCheck(
        "Market Data", SanityStatus.HEALTHY, f"last tick {int(age_seconds)}s ago"
    )


def _as_aware(moment: datetime_) -> datetime_:
    """SQLite (tests) can hand back a naive datetime for a
    tz-aware column — same defensive guard this project uses everywhere
    else it reads a timestamp back (see FundamentalFetchLogRepository's
    precedent)."""
    from datetime import UTC

    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


async def check_worker_heartbeat(
    session: AsyncSession, worker_name: str, settings: Settings
) -> ComponentCheck:
    heartbeat = await WorkerHeartbeatRepository(session).get(worker_name)
    if heartbeat is None or heartbeat.last_run_at is None:
        return ComponentCheck(worker_name, SanityStatus.UNKNOWN, "no heartbeat recorded yet")

    warning_s, stale_s = _thresholds_for(worker_name, settings)
    reference = heartbeat.last_success_at or heartbeat.last_run_at
    age_seconds = (utc_now() - _as_aware(reference)).total_seconds()
    detail = heartbeat.detail or ""

    if heartbeat.last_success_at is None:
        run_age_seconds = (utc_now() - _as_aware(heartbeat.last_run_at)).total_seconds()
        if detail.startswith(_BENIGN_SKIP_PREFIX) and run_age_seconds <= warning_s:
            # A worker (e.g. momentum_pipeline outside market hours) that
            # correctly determines "nothing to do" every cycle never gets
            # a ping_success — that's not the same as broken. The loop
            # itself pinging ping_run recently proves it's alive; only
            # flag WARNING for a worker that's actually trying and never
            # succeeding, or one that's never run at all yet.
            return ComponentCheck(
                worker_name,
                SanityStatus.NOT_CONFIGURED,
                f"alive, correctly idle — {detail}",
                last_success_at=None,
            )
        return ComponentCheck(
            worker_name,
            SanityStatus.WARNING,
            f"has run but never completed successfully ({detail})",
            last_success_at=None,
        )
    if age_seconds > stale_s:
        return ComponentCheck(
            worker_name,
            SanityStatus.STALE,
            f"no successful cycle in {int(age_seconds)}s (threshold {int(stale_s)}s) — {detail}",
            last_success_at=heartbeat.last_success_at,
        )
    if age_seconds > warning_s:
        return ComponentCheck(
            worker_name,
            SanityStatus.WARNING,
            f"last success {int(age_seconds)}s ago (threshold {int(warning_s)}s) — {detail}",
            last_success_at=heartbeat.last_success_at,
        )
    return ComponentCheck(
        worker_name,
        SanityStatus.HEALTHY,
        f"last success {int(age_seconds)}s ago — {detail}",
        last_success_at=heartbeat.last_success_at,
    )


def _thresholds_for(worker_name: str, settings: Settings) -> tuple[float, float]:
    if worker_name == MOMENTUM_PIPELINE_WORKER:
        return (
            settings.sanity_momentum_pipeline_warning_seconds,
            settings.sanity_momentum_pipeline_stale_seconds,
        )
    return (
        settings.sanity_worker_heartbeat_warning_seconds,
        settings.sanity_worker_heartbeat_stale_seconds,
    )


async def check_scanners(
    session: AsyncSession, settings: Settings, *, features_stale: bool
) -> list[ScannerCheck]:
    """Per-scanner: last run, latest result date, and whether the
    underlying `daily_features` data was fresh at check time. Zero
    results is never, on its own, treated as evidence of health — a
    scanner that hasn't produced a single scanner_results row still gets
    a real status derived from its own run history and the shared
    freshness signal, not silently reported HEALTHY by omission."""
    result_repo = ScannerResultRepository(session)
    run_repo = ScannerRunRepository(session)
    checks: list[ScannerCheck] = []

    for scanner_name in ALL_SCANNERS:
        recent_runs = await run_repo.get_recent(scanner_name=scanner_name, limit=1)
        last_run_at = recent_runs[0].finish_time if recent_runs else None

        latest_results = await result_repo.list_results(scanner_name=scanner_name, limit=1)
        latest_result_date = latest_results[0].date if latest_results else None

        if last_run_at is None:
            checks.append(
                ScannerCheck(
                    scanner_name,
                    SanityStatus.UNKNOWN,
                    "no scanner_runs recorded yet",
                    last_run_at=None,
                    latest_result_date=latest_result_date,
                )
            )
            continue

        if features_stale:
            checks.append(
                ScannerCheck(
                    scanner_name,
                    SanityStatus.BLOCKED,
                    "BLOCKED / STALE DATA — underlying daily_features is stale",
                    last_run_at=last_run_at,
                    latest_result_date=latest_result_date,
                )
            )
            continue

        age_seconds = (utc_now() - _as_aware(last_run_at)).total_seconds()
        warning_s = settings.sanity_worker_heartbeat_warning_seconds
        stale_s = settings.sanity_worker_heartbeat_stale_seconds
        if age_seconds > stale_s:
            status = SanityStatus.STALE
            detail = f"last run {int(age_seconds)}s ago (threshold {int(stale_s)}s)"
        elif age_seconds > warning_s:
            status = SanityStatus.WARNING
            detail = f"last run {int(age_seconds)}s ago"
        else:
            status = SanityStatus.HEALTHY
            detail = f"last run {int(age_seconds)}s ago"
        checks.append(
            ScannerCheck(
                scanner_name,
                status,
                detail,
                last_run_at=last_run_at,
                latest_result_date=latest_result_date,
            )
        )

    return checks
