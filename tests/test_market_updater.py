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
