"""Tests for app.sanity — the Project Sanity Check engine.

Most important test in this file: test_regression_daily_prices_current_daily_features_stale_is_critical
— the exact real incident (daily_prices current, daily_features several
trading days old) this whole system was built to catch."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import utc_now
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository, ScannerRunRepository
from app.repositories.worker_heartbeat_repository import WorkerHeartbeatRepository
from app.sanity import checks
from app.sanity.models import SanityStatus, worst_status
from app.sanity.service import SanityService

_FEATURES: dict[str, object] = {"trend_strength": "50"}


class _FakeRedis:
    """No real Redis needed — a minimal async double matching the two
    calls app.sanity.checks actually makes."""

    def __init__(self, *, alive: bool = True, groups: list[dict[str, object]] | None = None) -> None:
        self._alive = alive
        self._groups = groups if groups is not None else []

    async def ping(self) -> bool:
        if not self._alive:
            raise ConnectionError("simulated redis outage")
        return True

    async def xinfo_groups(self, stream_key: str) -> list[dict[str, object]]:
        return self._groups


async def _seed_symbol_with_prices_and_features(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    price_dates: list[date],
    feature_dates: list[date],
    symbol: str = "SANE",
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=f"tok-{symbol}")
        )
        await session.commit()
        await PriceRepository(session).upsert_daily_many(
            symbol_row.id,
            [
                Candle(
                    timestamp=datetime.combine(d, datetime.min.time()),
                    open=100, high=101, low=99, close=100, volume=1000,
                )
                for d in price_dates
            ],
        )
        for d in feature_dates:
            await DailyFeatureRepository(session).upsert(symbol_row.id, d, _FEATURES)
        await session.commit()
        return symbol_row.id


async def _seed_scanner_run_and_result(
    session_factory: async_sessionmaker[AsyncSession],
    symbol_id: int,
    scanner_name: str,
    *,
    run_finish_time: datetime,
    result_date: date,
) -> None:
    async with session_factory() as session:
        run = await ScannerRunRepository(session).start(scanner_name, run_finish_time)
        await ScannerRunRepository(session).finish(
            run,
            finish_time=run_finish_time,
            symbols_scanned=1,
            qualified_count=0,
            rejected_count=1,
            error_count=0,
        )
        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_id,
            scanner_name=scanner_name,
            date=result_date,
            score=Decimal("50.0"),
            status="rejected",
            reason="test",
            feature_snapshot={},
        )
        await session.commit()


def _engine(session_factory: async_sessionmaker[AsyncSession]) -> object:
    return session_factory.kw["bind"]


# --- Worker heartbeat checks ---


async def test_worker_heartbeat_unknown_when_never_pinged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(session, "feature_engine", Settings())
    assert result.status == SanityStatus.UNKNOWN


async def test_worker_heartbeat_healthy_when_recently_successful(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await WorkerHeartbeatRepository(session).ping_success("feature_engine", detail="ok")
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(session, "feature_engine", Settings())
    assert result.status == SanityStatus.HEALTHY
    assert result.last_success_at is not None


async def test_worker_heartbeat_stale_process_running_no_success(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The literal scenario section 4 asks for: process running (ping_run
    keeps moving) but no successful work for an abnormal period."""
    stale_time = utc_now() - timedelta(seconds=200_000)  # far beyond any threshold
    async with session_factory() as session:
        repo = WorkerHeartbeatRepository(session)
        await repo.ping_success("scanner_engine", detail="old success")
        await session.commit()
        row = await repo.get("scanner_engine")
        assert row is not None
        row.last_success_at = stale_time
        row.last_run_at = utc_now()  # still running recently
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(session, "scanner_engine", Settings())
    assert result.status == SanityStatus.STALE


async def test_worker_heartbeat_benign_skip_is_not_configured_not_warning(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """momentum_pipeline outside market hours: never gets a ping_success
    (there's genuinely nothing to evaluate), but pings ping_run every
    cycle with a 'skipped: ...' detail. That must read as informational
    (alive, correctly idle), not WARNING — a perpetually-closed-market
    day shouldn't look like a broken worker."""
    async with session_factory() as session:
        repo = WorkerHeartbeatRepository(session)
        await repo.ping_run(checks.MOMENTUM_PIPELINE_WORKER, detail="skipped: market_closed")
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(
            session, checks.MOMENTUM_PIPELINE_WORKER, Settings()
        )
    assert result.status == SanityStatus.NOT_CONFIGURED
    assert "alive, correctly idle" in result.detail


async def test_worker_heartbeat_genuine_no_success_still_warns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A worker that's run but never succeeded, and ISN'T a benign skip,
    must still surface as WARNING — the fix above is specifically scoped
    to the 'skipped:' convention, not a blanket suppression."""
    async with session_factory() as session:
        repo = WorkerHeartbeatRepository(session)
        await repo.ping_run("feature_engine", detail="attempted, threw an exception")
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(session, "feature_engine", Settings())
    assert result.status == SanityStatus.WARNING


async def test_worker_heartbeat_stale_benign_skip_pings_still_flagged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """If even the skip-pings stop arriving (the scheduler loop itself
    died), that's a real problem again, not a benign idle state."""
    old = utc_now() - timedelta(minutes=20)
    async with session_factory() as session:
        repo = WorkerHeartbeatRepository(session)
        await repo.ping_run(checks.MOMENTUM_PIPELINE_WORKER, detail="skipped: market_closed")
        await session.commit()
        row = await repo.get(checks.MOMENTUM_PIPELINE_WORKER)
        assert row is not None
        row.last_run_at = old
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(
            session, checks.MOMENTUM_PIPELINE_WORKER, Settings()
        )
    assert result.status == SanityStatus.WARNING  # not NOT_CONFIGURED — the idle-skip claim is stale


async def test_worker_heartbeat_uses_tighter_momentum_pipeline_thresholds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """momentum_pipeline runs every 60s — 20 minutes of silence must read
    STALE even though that's well within the generic event-driven
    worker's tolerance."""
    old = utc_now() - timedelta(minutes=20)
    async with session_factory() as session:
        repo = WorkerHeartbeatRepository(session)
        await repo.ping_success(checks.MOMENTUM_PIPELINE_WORKER, detail="tick")
        await session.commit()
        row = await repo.get(checks.MOMENTUM_PIPELINE_WORKER)
        assert row is not None
        row.last_success_at = old
        row.last_run_at = old
        await session.commit()

    async with session_factory() as session:
        result = await checks.check_worker_heartbeat(
            session, checks.MOMENTUM_PIPELINE_WORKER, Settings()
        )
    assert result.status == SanityStatus.STALE

    async with session_factory() as session:
        # The same 20-minute gap is only WARNING/HEALTHY for an
        # event-driven worker under the generic (6h/24h) thresholds.
        other = await checks.check_worker_heartbeat(session, "feature_engine", Settings())
    # feature_engine was never pinged in this test -> UNKNOWN, not STALE;
    # the point is momentum_pipeline's tighter threshold fired on its own.
    assert other.status == SanityStatus.UNKNOWN


# --- Data freshness (the core regression) ---


async def test_regression_daily_prices_current_daily_features_stale_is_critical(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """THE regression test: daily_prices current + daily_features stale
    must be reported as a critical/stale condition, never as healthy
    just because the process is running."""
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)],
        feature_dates=[date(2026, 8, 11)],
    )

    async with session_factory() as session:
        prices_check, features_check, freshness = await checks.check_data_freshness(
            session, Settings()
        )

    assert prices_check.status == SanityStatus.HEALTHY
    assert features_check.status == SanityStatus.STALE
    assert freshness.is_stale is True
    assert "daily_features" in features_check.name
    overall = worst_status([prices_check.status, features_check.status])
    assert overall == SanityStatus.STALE


async def test_data_freshness_healthy_when_dates_match(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[date(2026, 8, 14)]
    )
    async with session_factory() as session:
        prices_check, features_check, _ = await checks.check_data_freshness(session, Settings())
    assert prices_check.status == SanityStatus.HEALTHY
    assert features_check.status == SanityStatus.HEALTHY


async def test_data_freshness_failed_when_features_never_computed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[]
    )
    async with session_factory() as session:
        _, features_check, _ = await checks.check_data_freshness(session, Settings())
    assert features_check.status == SanityStatus.FAILED


# --- Market calendar check ---


def test_market_calendar_healthy_on_a_trading_day() -> None:
    # 2026-01-27 11:00 IST (05:30 UTC) — Tuesday, no holiday.
    now = datetime(2026, 1, 27, 5, 30, tzinfo=UTC)
    result = checks.check_market_calendar(Settings(), now=now)
    assert result.status == SanityStatus.HEALTHY
    assert "Today: Trading Day" in result.detail
    assert "Next Trading Day: 2026-01-28" in result.detail
    assert "Previous Trading Day: 2026-01-26" not in result.detail  # 26th is a holiday, not prior


def test_market_calendar_reports_nse_holiday() -> None:
    # 2026-01-26 11:00 IST — Republic Day.
    now = datetime(2026, 1, 26, 5, 30, tzinfo=UTC)
    result = checks.check_market_calendar(Settings(), now=now)
    assert result.status == SanityStatus.HEALTHY
    assert "NSE Holiday: Republic Day" in result.detail
    assert "Previous Trading Day: 2026-01-23" in result.detail
    assert "Next Trading Day: 2026-01-27" in result.detail


def test_market_calendar_flags_equity_fno_divergence() -> None:
    # 2026-01-15 — Equity holiday, F&O trades.
    now = datetime(2026, 1, 15, 5, 30, tzinfo=UTC)
    result = checks.check_market_calendar(Settings(), now=now)
    assert "NSE Holiday: Municipal Corporation Election" in result.detail
    assert "F&O segment trades today, differs from Equity" in result.detail


def test_market_calendar_not_configured_for_unverified_year() -> None:
    now = datetime(2027, 1, 5, 5, 30, tzinfo=UTC)
    result = checks.check_market_calendar(Settings(), now=now)
    assert result.status == SanityStatus.NOT_CONFIGURED
    assert "No verified NSE" in result.detail


# --- Scanner checks ---


async def test_scanner_blocked_when_features_stale(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[date(2026, 8, 11)]
    )
    await _seed_scanner_run_and_result(
        session_factory, symbol_id, "breakout_v1",
        run_finish_time=utc_now(), result_date=date(2026, 8, 11),
    )

    async with session_factory() as session:
        scanner_checks = await checks.check_scanners(session, Settings(), features_stale=True)

    breakout = next(c for c in scanner_checks if c.scanner_name == "breakout_v1")
    assert breakout.status == SanityStatus.BLOCKED
    assert "STALE DATA" in breakout.detail


async def test_scanner_not_healthy_merely_because_zero_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A scanner with zero scanner_results rows (never run) must be
    UNKNOWN, never silently HEALTHY."""
    async with session_factory() as session:
        scanner_checks = await checks.check_scanners(session, Settings(), features_stale=False)
    breakout = next(c for c in scanner_checks if c.scanner_name == "breakout_v1")
    assert breakout.status == SanityStatus.UNKNOWN
    assert breakout.status != SanityStatus.HEALTHY


async def test_scanner_healthy_with_fresh_features_and_recent_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[date(2026, 8, 14)]
    )
    await _seed_scanner_run_and_result(
        session_factory, symbol_id, "vcp_v1",
        run_finish_time=utc_now(), result_date=date(2026, 8, 14),
    )
    async with session_factory() as session:
        scanner_checks = await checks.check_scanners(session, Settings(), features_stale=False)
    vcp = next(c for c in scanner_checks if c.scanner_name == "vcp_v1")
    assert vcp.status == SanityStatus.HEALTHY


# --- Infra checks ---


async def test_database_healthy_when_reachable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await checks.check_database(_engine(session_factory))
    assert result.status == SanityStatus.HEALTHY


async def test_database_failed_when_unreachable() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    broken_engine = create_async_engine("postgresql+asyncpg://nouser:nopass@127.0.0.1:1/nodb")
    result = await checks.check_database(broken_engine)
    assert result.status == SanityStatus.FAILED
    await broken_engine.dispose()


async def test_redis_healthy_when_reachable() -> None:
    result = await checks.check_redis(_FakeRedis(alive=True), Settings())
    assert result.status == SanityStatus.HEALTHY


async def test_redis_failed_when_unreachable() -> None:
    result = await checks.check_redis(_FakeRedis(alive=False), Settings())
    assert result.status == SanityStatus.FAILED


async def test_redis_reports_stream_lag_in_detail() -> None:
    fake = _FakeRedis(
        alive=True,
        groups=[{"name": "pipeline_workers", "pending": 0, "lag": 0}],
    )
    result = await checks.check_redis(fake, Settings())
    assert "pending=0" in result.detail


# --- Market data pipeline ---


async def test_market_data_pipeline_not_stale_when_market_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await checks.check_market_data_pipeline(
            session, Settings(), is_market_open=False
        )
    assert result.status == SanityStatus.NOT_CONFIGURED  # informational, not a failure


async def test_market_data_pipeline_failed_when_market_open_but_no_ticks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await checks.check_market_data_pipeline(session, Settings(), is_market_open=True)
    assert result.status == SanityStatus.FAILED


# --- worst_status combining ---


# --- IST timezone-configuration check ---


def test_timezone_configuration_healthy_when_ist() -> None:
    result = checks.check_timezone_configuration(Settings())
    assert result.status == SanityStatus.HEALTHY
    assert "Asia/Kolkata" in result.detail


def test_timezone_configuration_warns_when_misconfigured() -> None:
    result = checks.check_timezone_configuration(Settings(market_timezone="UTC"))
    assert result.status == SanityStatus.WARNING


async def test_sanity_report_carries_ist_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SanityService(
        session_factory,
        _engine(session_factory),
        Settings(),
        _FakeRedis(alive=True),
        is_market_open=False,
    )
    report = await service.run()
    assert report.market_timezone == "Asia/Kolkata"
    assert report.checked_at_ist is not None
    assert report.current_market_date is not None
    assert report.checked_at_ist.utcoffset().total_seconds() == 5.5 * 3600
    body = report.to_dict()
    assert body["market_timezone"] == "Asia/Kolkata"
    assert "checked_at_ist" in body


def test_worst_status_picks_the_most_severe() -> None:
    assert worst_status([SanityStatus.HEALTHY, SanityStatus.WARNING]) == SanityStatus.WARNING
    assert worst_status([SanityStatus.HEALTHY, SanityStatus.STALE, SanityStatus.WARNING]) == SanityStatus.STALE
    assert worst_status([SanityStatus.FAILED, SanityStatus.HEALTHY]) == SanityStatus.FAILED
    assert worst_status([]) == SanityStatus.UNKNOWN
    assert worst_status([SanityStatus.HEALTHY, SanityStatus.NOT_CONFIGURED]) == SanityStatus.HEALTHY


# --- Full SanityService.run() end to end ---


async def test_sanity_service_reports_healthy_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[date(2026, 8, 14)]
    )
    await _seed_scanner_run_and_result(
        session_factory, symbol_id, "breakout_v1",
        run_finish_time=utc_now(), result_date=date(2026, 8, 14),
    )
    async with session_factory() as session:
        for worker in (*checks.EVENT_DRIVEN_WORKERS, checks.MOMENTUM_PIPELINE_WORKER):
            await WorkerHeartbeatRepository(session).ping_success(worker, detail="ok")
        await session.commit()

    service = SanityService(
        session_factory,
        _engine(session_factory),
        Settings(),
        _FakeRedis(alive=True),
        is_market_open=False,
    )
    report = await service.run()

    assert report.daily_prices_latest_date == date(2026, 8, 14)
    assert report.daily_features_latest_date == date(2026, 8, 14)
    assert report.overall_status in (SanityStatus.HEALTHY, SanityStatus.UNKNOWN, SanityStatus.WARNING)
    assert isinstance(report.to_dict(), dict)


async def test_sanity_service_reports_stale_end_to_end_matching_the_real_incident(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)],
        feature_dates=[date(2026, 8, 11)],
    )

    service = SanityService(
        session_factory,
        _engine(session_factory),
        Settings(),
        _FakeRedis(alive=True),
        is_market_open=False,
    )
    report = await service.run()

    assert report.overall_status == SanityStatus.STALE
    assert any("daily_features" in issue for issue in report.issues)


async def test_sanity_service_reports_failed_when_database_unreachable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    broken_engine = create_async_engine("postgresql+asyncpg://nouser:nopass@127.0.0.1:1/nodb")
    service = SanityService(
        session_factory, broken_engine, Settings(), _FakeRedis(alive=True), is_market_open=False
    )
    report = await service.run()
    assert report.overall_status == SanityStatus.FAILED
    await broken_engine.dispose()


async def test_sanity_service_reports_failed_when_redis_unreachable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory, price_dates=[date(2026, 8, 14)], feature_dates=[date(2026, 8, 14)]
    )
    service = SanityService(
        session_factory,
        _engine(session_factory),
        Settings(),
        _FakeRedis(alive=False),
        is_market_open=False,
    )
    report = await service.run()
    assert report.overall_status == SanityStatus.FAILED
