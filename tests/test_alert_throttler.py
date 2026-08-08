"""Tests for app.alerts.throttler.AlertThrottler — configurable cooldown."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.throttler import AlertThrottler
from app.config.settings import Settings
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository


async def test_cooldown_suppresses_recent_alert_for_same_signal(
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
            fingerprint="fp-1",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await session.commit()

        throttler = AlertThrottler(alert_repo, Settings(alert_cooldown_minutes=30))
        result = await throttler.check(symbol.id, "BREAKOUT", now=datetime.now())
        assert result.suppressed is True
        assert result.blocking_alert_id is not None


async def test_cooldown_allows_after_window_elapses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        alert_repo = AlertRepository(session)
        throttler = AlertThrottler(alert_repo, Settings(alert_cooldown_minutes=30))

        # No prior alert -> not suppressed.
        result = await throttler.check(symbol.id, "BREAKOUT", now=datetime.now())
        assert result.suppressed is False

        # A prior alert far enough in the past (relative to `now`) doesn't
        # fall inside the cooldown window either.
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

        far_future = datetime.now() + timedelta(minutes=45)
        result = await throttler.check(symbol.id, "BREAKOUT", now=far_future)
        assert result.suppressed is False


async def test_cooldown_scoped_per_signal_type(
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
            fingerprint="fp-1",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await session.commit()

        throttler = AlertThrottler(alert_repo, Settings(alert_cooldown_minutes=30))
        result = await throttler.check(symbol.id, "MOMENTUM", now=datetime.now())
        assert result.suppressed is False
