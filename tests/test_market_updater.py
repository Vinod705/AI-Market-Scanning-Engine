"""Tests for app.data.market_updater."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.market_updater import MarketStatusUpdater

_IST = ZoneInfo("Asia/Kolkata")


def test_is_market_open_during_session() -> None:
    tuesday_11am = datetime(2026, 1, 6, 11, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(tuesday_11am) is True


def test_is_market_open_before_open() -> None:
    tuesday_8am = datetime(2026, 1, 6, 8, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(tuesday_8am) is False


def test_is_market_open_after_close() -> None:
    tuesday_5pm = datetime(2026, 1, 6, 17, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(tuesday_5pm) is False


def test_is_market_open_on_weekend() -> None:
    saturday_11am = datetime(2026, 1, 10, 11, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(saturday_11am) is False


def test_is_market_open_false_on_nse_holiday() -> None:
    """2026-01-26 (Monday, Republic Day) is a weekday within trading
    hours — the pre-calendar version of this function would have
    incorrectly said the market was open."""
    republic_day_11am = datetime(2026, 1, 26, 11, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(republic_day_11am) is False


def test_is_market_open_false_on_equity_only_holiday() -> None:
    """2026-01-15 is an Equity-segment-only NSE closure — is_market_open
    uses the Equity calendar (see its docstring), so this must be False
    even though F&O trades that day."""
    jan_15_11am = datetime(2026, 1, 15, 11, 0, tzinfo=_IST)
    assert MarketStatusUpdater.is_market_open(jan_15_11am) is False


def test_is_market_open_falls_back_gracefully_for_unverified_year() -> None:
    """No verified NSE calendar exists for 2030 yet — this must degrade
    to the pre-calendar weekday+hours check (visibly logged), never
    crash and never guess holiday dates."""
    unverified_tuesday_11am = datetime(2030, 1, 8, 11, 0, tzinfo=_IST)
    assert unverified_tuesday_11am.weekday() < 5
    assert MarketStatusUpdater.is_market_open(unverified_tuesday_11am) is True


async def test_record_success_updates_market_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    updater = MarketStatusUpdater(session_factory)

    await updater.record_success(provider_connected=True)

    async with session_factory() as session:
        from app.repositories.market_repository import MarketStatusRepository

        status = await MarketStatusRepository(session).get()
        assert status is not None
        assert status.provider_connected is True
        assert status.last_success is not None
        assert status.last_failure is None


async def test_record_failure_updates_market_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    updater = MarketStatusUpdater(session_factory)

    await updater.record_failure(provider_connected=False)

    async with session_factory() as session:
        from app.repositories.market_repository import MarketStatusRepository

        status = await MarketStatusRepository(session).get()
        assert status is not None
        assert status.provider_connected is False
        assert status.last_failure is not None
