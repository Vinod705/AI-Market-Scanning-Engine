"""Timezone-correctness tests for the UTC-internal / IST-at-boundary
convention (see app.core.time).

These deliberately do NOT depend on the host OS's timezone (flipping it via
`time.tzset` isn't available on Windows, and even where it is, it's a
process-global side effect that's fragile to run tests under). Instead each
test constructs explicit aware datetimes that reproduce the exact class of
bug this convention prevents: a UTC instant that falls on a different
calendar day in IST than in UTC, or a naive value that would have silently
been treated as "whatever the host happens to be" before this fix. If any of
these ever regress to comparing a naive `datetime.now()` against an aware
column/instant again, they fail regardless of what timezone the CI runner
itself is in.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.throttler import AlertThrottler
from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.decision.models import DecisionCandidate
from app.decision.rules import check_data_freshness
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository

_IST = "Asia/Kolkata"


def test_utc_now_is_timezone_aware_utc() -> None:
    moment = utc_now()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_to_market_time_converts_aware_utc_instant_to_ist() -> None:
    # 18:35 UTC = 00:05 IST the *next* day — the exact boundary window where
    # a bare UTC calendar date and the NSE trading-calendar (IST) date
    # diverge.
    moment = datetime(2026, 1, 5, 18, 35, tzinfo=UTC)
    ist = to_market_time(moment, _IST)
    assert ist.date() == date(2026, 1, 6)
    assert (ist.hour, ist.minute) == (0, 5)


def test_to_market_time_treats_naive_input_as_utc_not_host_local() -> None:
    # A naive datetime must be interpreted as UTC (this codebase's
    # convention), never as "whatever the host OS timezone is" — that
    # host-dependent interpretation is the root cause this fix removes.
    naive = datetime(2026, 1, 5, 18, 35)
    aware = datetime(2026, 1, 5, 18, 35, tzinfo=UTC)
    assert to_market_time(naive, _IST) == to_market_time(aware, _IST)


def test_check_data_freshness_uses_market_calendar_day_not_utc_day() -> None:
    """A scan_date of Jan 6 must be judged "fresh" (age 0) when `now` is an
    instant that's already Jan 6 in IST, even though it's still Jan 5 in
    UTC. Using the raw UTC date here would make this scan_date look 1 day
    stale purely from the timezone boundary, not from any real staleness."""
    settings = Settings(decision_max_data_age_days=0, market_timezone=_IST)
    candidate = DecisionCandidate(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        score=91.0,
        scan_date=date(2026, 1, 6),
        feature_snapshot={},
    )
    now_utc = datetime(2026, 1, 5, 19, 0, tzinfo=UTC)  # 00:30 IST on Jan 6

    result = check_data_freshness(candidate, settings, now=now_utc)

    assert result.status.value == "PASS"


def test_check_data_freshness_rejects_stale_scan_date_in_market_calendar() -> None:
    settings = Settings(decision_max_data_age_days=0, market_timezone=_IST)
    candidate = DecisionCandidate(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        score=91.0,
        scan_date=date(2026, 1, 5),  # yesterday in IST relative to `now_utc`
        feature_snapshot={},
    )
    now_utc = datetime(2026, 1, 5, 19, 0, tzinfo=UTC)  # 00:30 IST on Jan 6

    result = check_data_freshness(candidate, settings, now=now_utc)

    assert result.status.value == "FAIL"


async def test_alert_cooldown_correct_when_db_and_python_clocks_are_both_utc_aware(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the original C3 bug directly: previously, comparing a
    naive `datetime.now()` against the DB's aware `created_at` silently
    returned the wrong answer on any host whose OS clock wasn't UTC (this
    was caught by this suite running on an IST dev machine). Using
    `utc_now()` end to end must suppress correctly regardless of what "now"
    the test constructs it relative to."""
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        alert_repo = AlertRepository(session)
        await alert_repo.create(
            symbol_id=symbol.id,
            scanner_name="breakout_v1",
            signal_type="BREAKOUT",
            decision="ALERT",
            score=Decimal("91"),
            quality="HIGH",
            entry_reference=None,
            breakout_level=None,
            support_level=None,
            resistance_level=None,
            feature_snapshot={},
            reason="ok",
            passed_rules=[],
            fingerprint="fp-1",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await session.commit()

        throttler = AlertThrottler(alert_repo, Settings(alert_cooldown_minutes=30))
        result = await throttler.check(symbol.id, "BREAKOUT", now=utc_now())

        assert result.suppressed is True
