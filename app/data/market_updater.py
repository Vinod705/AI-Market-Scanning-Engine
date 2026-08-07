"""Market status updates: the current market-hours/provider-health snapshot."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.market_repository import MarketStatusRepository

_NSE_TZ = ZoneInfo("Asia/Kolkata")
_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)


class MarketStatusUpdater:
    """Keeps the singleton `market_status` row current."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def is_market_open(now: datetime | None = None) -> bool:
        """NSE regular session, Mon-Fri 09:15-15:30 IST.

        Does not account for exchange holidays — a full trading calendar is
        out of scope for Phase 2.
        """
        moment = (now or datetime.now(_NSE_TZ)).astimezone(_NSE_TZ)
        if moment.weekday() >= 5:  # Saturday, Sunday
            return False
        return _NSE_OPEN <= moment.time() <= _NSE_CLOSE

    async def record_success(self, *, provider_connected: bool) -> None:
        now = datetime.now(_NSE_TZ)
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
        now = datetime.now(_NSE_TZ)
        async with self._session_factory() as session:
            repo = MarketStatusRepository(session)
            await repo.upsert(
                market_open=self.is_market_open(now),
                provider_connected=provider_connected,
                last_update=now,
                last_failure=now,
            )
            await session.commit()
