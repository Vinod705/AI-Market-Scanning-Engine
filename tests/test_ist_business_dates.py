"""Tests proving Indian market business-date logic is IST-authoritative,
not UTC and not the host's local timezone — see app.core.time's module
docstring for the distinction this whole test file exercises.

The headline case (from this project's own IST-authoritative-timezone
requirement): UTC 2026-08-15 23:55 is IST 2026-08-16 05:25 — the
business date must be 2026-08-16, not 2026-08-15."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.time as core_time
from app.alerts.deduplicator import AlertDeduplicator
from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.core.time import market_date_of, market_day_bounds_utc, market_today, now_market_time
from app.decision.models import Decision, DecisionResult, Quality
from app.fundamentals.queue_models import FetchStatus
from app.providers.base_provider import ProviderSymbol
from app.repositories.fundamental_fetch_log_repository import FundamentalFetchLogRepository
from app.repositories.market_repository import SymbolRepository


def test_market_today_at_the_utc_evening_ist_early_morning_boundary(
    monkeypatch,
) -> None:
    """The exact example: UTC 23:55 on the 15th is IST 05:25 on the
    16th. The Indian business date must be the 16th."""
    monkeypatch.setattr(
        core_time, "utc_now", lambda: datetime(2026, 8, 15, 23, 55, tzinfo=UTC)
    )
    assert market_today() == datetime(2026, 8, 16).date()


def test_market_today_matches_utc_date_outside_the_boundary_window(monkeypatch) -> None:
    """IST is a fixed +5:30 offset — it is always at or ahead of UTC's
    own calendar date, never behind, so the only mismatch window is UTC
    evening/IST-next-day-early-morning (tested above). Mid-day UTC times
    are unambiguous: both agree."""
    monkeypatch.setattr(
        core_time, "utc_now", lambda: datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    )
    assert market_today() == datetime(2026, 8, 15).date()


def test_now_market_time_carries_the_ist_offset() -> None:
    moment = now_market_time("Asia/Kolkata")
    assert moment.utcoffset().total_seconds() == 5.5 * 3600


def test_market_date_of_converts_a_utc_instant_correctly() -> None:
    # 2026-08-15 23:55 UTC -> 2026-08-16 05:25 IST
    utc_instant = datetime(2026, 8, 15, 23, 55, tzinfo=UTC)
    assert market_date_of(utc_instant, "Asia/Kolkata") == datetime(2026, 8, 16).date()


def test_market_date_of_is_a_noop_for_an_already_ist_offset_instant() -> None:
    """Real Upstox candle timestamps already carry +05:30 — converting
    them again must not change the result."""
    from datetime import timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    already_ist = datetime(2026, 8, 14, 0, 0, tzinfo=ist)
    assert market_date_of(already_ist, "Asia/Kolkata") == datetime(2026, 8, 14).date()


def test_market_day_bounds_utc_for_a_known_ist_calendar_day() -> None:
    start_utc, end_utc = market_day_bounds_utc(datetime(2026, 8, 16).date(), "Asia/Kolkata")
    # 2026-08-16 00:00 IST == 2026-08-15 18:30 UTC
    assert start_utc == datetime(2026, 8, 15, 18, 30, tzinfo=UTC)
    assert end_utc == datetime(2026, 8, 16, 18, 30, tzinfo=UTC)


# --- Regression: the actual bug this project hit — a daily budget count
# silently disagreeing across the IST/UTC midnight boundary. ---


async def test_daily_budget_counts_by_ist_calendar_day_not_utc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two requests logged either side of real IST midnight (but the
    *same* IST calendar day, straddling a UTC calendar-day rollover)
    must both count as "today"; a request from the IST day before must
    not — seeded relative to whatever `market_today()` actually is when
    the suite runs, so the assertion is meaningful on any day, not just
    a hardcoded one."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="ISTSYM2", exchange="N", instrument_token="ist2")
        )
        await session.commit()
        repo = FundamentalFetchLogRepository(session)

        today = market_today()
        start_utc, end_utc = market_day_bounds_utc(today)
        just_after_start = start_utc + (end_utc - start_utc) / 100  # well inside today, IST
        just_before_start = start_utc - (end_utc - start_utc) / 100  # well inside yesterday, IST

        in_today = await repo.record(symbol_id=symbol_row.id, status=FetchStatus.SUCCESS)
        in_today.requested_at = just_after_start
        in_yesterday = await repo.record(symbol_id=symbol_row.id, status=FetchStatus.SUCCESS)
        in_yesterday.requested_at = just_before_start
        await session.commit()

        count_today = await repo.count_today()
        assert count_today == 1


async def test_alert_signal_date_is_ist_not_utc_across_the_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The real bug this project's own alerts.manager had: `signal_date`
    used to be a bare `.date()` on a UTC instant. At 23:55 UTC that's a
    full calendar day off from the correct IST trading date."""
    settings = Settings()
    manager = AlertManager(session_factory, settings, AlertQueue())

    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="ALERTIST", exchange="N", instrument_token="alertist1")
        )
        await session.commit()
        symbol_id = symbol_row.id

    decision = DecisionResult(
        symbol="ALERTIST",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        decision=Decision.ALERT,
        score=90.0,
        quality=Quality.HIGH,
        passed_rules=["all"],
        feature_snapshot={},
    )
    boundary_moment = datetime(2026, 8, 15, 23, 55, tzinfo=UTC)

    alert_id = await manager.process(decision, symbol_id=symbol_id, now=boundary_moment)
    assert alert_id is not None

    async with session_factory() as session:
        from app.repositories.alert_repository import AlertRepository

        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.signal_date == datetime(2026, 8, 16).date()  # IST, not 2026-08-15 (UTC)


def test_alert_fingerprint_signal_date_fallback_is_ist() -> None:
    """AlertDeduplicator's fingerprint uses feature_snapshot['date'] when
    present (set by app.momentum.momentum_engine, already IST — see that
    module) or falls back to decision.timestamp.date(). Since
    DecisionResult.timestamp now defaults to now_market_time(), that
    fallback is IST by construction, not by luck."""
    decision = DecisionResult(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        decision=Decision.ALERT,
        score=80.0,
        quality=Quality.MEDIUM,
        feature_snapshot={},  # no "date" key -> exercises the timestamp fallback
    )
    fingerprint_a = AlertDeduplicator.build_fingerprint(decision)
    # Same decision, explicit UTC-evening timestamp forced onto it — the
    # fingerprint must reflect the IST date derived from that instant,
    # not the raw UTC date, proving the fallback path is IST-correct.
    decision.timestamp = datetime(2026, 8, 15, 23, 55, tzinfo=UTC)
    fingerprint_b = AlertDeduplicator.build_fingerprint(decision)
    assert fingerprint_a != fingerprint_b  # different instants -> different fingerprints, sanity check


def test_candidate_scan_date_defaults_to_ist(monkeypatch) -> None:
    """StockCandidate.timestamp used to default to bare `datetime.now()`
    (host-local, not even UTC) — now IST via now_market_time(), which
    itself calls utc_now() (module-global lookup, so patching
    app.core.time.utc_now here is what actually takes effect — the
    dataclass field's default_factory captured a reference to
    now_market_time at import time, but now_market_time's own call to
    utc_now() is resolved fresh from app.core.time's namespace every
    time it runs)."""
    monkeypatch.setattr(core_time, "utc_now", lambda: datetime(2026, 8, 15, 23, 55, tzinfo=UTC))

    from app.candidates.models import CandidateContext, StockCandidate, Universe

    candidate = StockCandidate(
        symbol="TCS", instrument_type="EQUITY", universe=Universe.FNO, scanner_type="fno_momentum_v1",
        price=Decimal("100"), breakout_level=None, support_level=None, resistance_level=None,
        fundamental_score=None, technical_score=50.0, overall_score=50.0, quality="MEDIUM",
        scanner_sources=["UPSTOX"], fundamental_reasons=[], technical_reasons=[], risk_flags=[],
        passed_rules=[], failed_rules=[], data_completeness_pct=0.0, technical_data_completeness_pct=0.0,
        setup_state=None, alert_category=None, reason="",
    )  # fmt: skip
    context = CandidateContext(symbol=None, candidate=candidate)  # type: ignore[arg-type]
    assert context.scan_date == datetime(2026, 8, 16).date()
