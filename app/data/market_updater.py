"""Market status updates: the current market-hours/provider-health snapshot."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.repositories.market_repository import MarketStatusRepository


class MarketStatusUpdater:
    """Keeps the singleton `market_status` row current.

    Market hours (timezone, open/close time) are read from `Settings` rather
    than hardcoded, so the Phase 5 decision engine's session-validity rule
    shares the exact same "is the market open" definition used here instead
    of reimplementing it.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def is_market_open(now: datetime | None = None) -> bool:
        """NSE regular session, Mon-Fri, per `Settings.market_open_time` /
        `market_close_time` / `market_timezone` (defaults 09:15-15:30 IST).

        Does not account for exchange holidays — a full trading calendar is
        out of scope for this project.
        """
        settings = get_settings()
        tz = ZoneInfo(settings.market_timezone)
        moment = (now or datetime.now(tz)).astimezone(tz)
        if moment.weekday() >= 5:  # Saturday, Sunday
            return False
        return settings.market_open_time <= moment.time() <= settings.market_close_time

    async def record_success(self, *, provider_connected: bool) -> None:
        now = datetime.now(ZoneInfo(get_settings().market_timezone))
        async with self._session_factory() as session:
            repo = MarketStatusRepository(session)
            await repo.upsert(
                market_open=self.is_market_open(now),
                provider_connected=provider_connected,
                last_update=now,
                last_success=now,
            )
            await session.commit()

    async def record_failure(self, *, provider_connected: bool) -> None:
        now = datetime.now(ZoneInfo(get_settings().market_timezone))
        async with self._session_factory() as session:
            repo = MarketStatusRepository(session)
            await repo.upsert(
                market_open=self.is_market_open(now),
                provider_connected=provider_connected,
                last_update=now,
                last_failure=now,
            )
            await session.commit()
