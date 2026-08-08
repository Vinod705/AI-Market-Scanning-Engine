"""Tests for app.alerts.deduplicator.AlertDeduplicator."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.deduplicator import AlertDeduplicator
from app.decision.models import Decision, DecisionResult, Quality
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository


def _decision(**overrides: object) -> DecisionResult:
    defaults: dict[str, object] = dict(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        decision=Decision.ALERT,
        score=91.0,
        quality=Quality.HIGH,
        feature_snapshot={"breakout_level": "842", "date": "2026-01-05"},
        timestamp=datetime(2026, 1, 5, 10, 0),
    )
    defaults.update(overrides)
    return DecisionResult(**defaults)  # type: ignore[arg-type]


def test_build_fingerprint_is_deterministic() -> None:
    a = AlertDeduplicator.build_fingerprint(_decision())
    b = AlertDeduplicator.build_fingerprint(_decision())
    assert a == b


def test_build_fingerprint_differs_by_symbol() -> None:
    a = AlertDeduplicator.build_fingerprint(_decision(symbol="TCS"))
    b = AlertDeduplicator.build_fingerprint(_decision(symbol="INFY"))
    assert a != b


def test_build_fingerprint_differs_by_breakout_level() -> None:
    a = AlertDeduplicator.build_fingerprint(
        _decision(feature_snapshot={"breakout_level": "842", "date": "2026-01-05"})
    )
    b = AlertDeduplicator.build_fingerprint(
        _decision(feature_snapshot={"breakout_level": "900", "date": "2026-01-05"})
    )
    assert a != b


def test_build_fingerprint_same_across_repeated_same_day_scans() -> None:
    """The scanner runs every minute — repeated candidates for the same
    setup on the same day must hash to the same fingerprint."""
    first = AlertDeduplicator.build_fingerprint(_decision(timestamp=datetime(2026, 1, 5, 9, 20)))
    second = AlertDeduplicator.build_fingerprint(_decision(timestamp=datetime(2026, 1, 5, 9, 21)))
    assert first == second


async def test_check_suppresses_active_duplicate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
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
            fingerprint="fp-active",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await session.commit()

        result = await AlertDeduplicator(alert_repo).check("fp-active")
        assert result.suppressed is True
        assert result.blocking_alert_id is not None


async def test_check_allows_new_signal_when_previous_expired(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        alert_repo = AlertRepository(session)
        expired_alert = await alert_repo.create(
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
            fingerprint="fp-expired",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await alert_repo.update_status(expired_alert, "EXPIRED")
        await session.commit()

        result = await AlertDeduplicator(alert_repo).check("fp-expired")
        assert result.suppressed is False


async def test_check_allows_unseen_fingerprint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await AlertDeduplicator(AlertRepository(session)).check("fp-never-seen")
        assert result.suppressed is False
