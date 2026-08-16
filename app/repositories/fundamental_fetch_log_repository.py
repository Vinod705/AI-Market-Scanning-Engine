"""Repository for `fundamental_fetch_log` — the Fundamental Queue's
durable, restart-safe record of every Trendlyne fetch attempt."""

from datetime import UTC
from datetime import datetime as datetime_

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import market_day_bounds_utc, market_today
from app.fundamentals.queue_models import FetchStatus
from app.models.fundamental_fetch_log import FundamentalFetchLog


class FundamentalFetchLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, *, symbol_id: int, status: FetchStatus, error_message: str | None = None
    ) -> FundamentalFetchLog:
        row = FundamentalFetchLog(
            symbol_id=symbol_id, status=status.value, error_message=error_message
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def count_today(self, *, status: FetchStatus | None = None) -> int:
        # `requested_at` is stored UTC (`DateTime(timezone=True)`,
        # `server_default=func.now()`) — but "today" is an Indian market
        # business date, so it must be judged against the IST calendar
        # day, not UTC's and not the host's local system date. Comparing
        # a UTC-truncated `func.date(requested_at)` against an IST `date`
        # (or vice versa) silently mismatches for ~5.5 hours around IST
        # midnight — this call site originally used `date.today()` (host
        # local), was fixed once to UTC, and is now fixed properly to
        # IST via an explicit UTC range comparison (see
        # `app.core.time.market_day_bounds_utc`'s docstring for why a
        # range, not `func.date()`, is the correct approach here).
        start_utc, end_utc = market_day_bounds_utc(market_today())
        stmt = (
            select(func.count())
            .select_from(FundamentalFetchLog)
            .where(
                FundamentalFetchLog.requested_at >= start_utc,
                FundamentalFetchLog.requested_at < end_utc,
            )
        )
        if status is not None:
            stmt = stmt.where(FundamentalFetchLog.status == status.value)
        return (await self._session.execute(stmt)).scalar_one()

    async def last_requested_at(self) -> datetime_ | None:
        stmt = select(func.max(FundamentalFetchLog.requested_at))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def most_recent_rate_limit(self) -> datetime_ | None:
        """The timestamp of the most recent RATE_LIMITED entry that is more
        recent than the most recent SUCCESS/CACHED entry — i.e. "are we
        still in a rate-limited state right now". Returns None once a
        later successful/cached fetch has happened."""
        latest_rate_limited = select(func.max(FundamentalFetchLog.requested_at)).where(
            FundamentalFetchLog.status == FetchStatus.RATE_LIMITED.value
        )
        rate_limited_at = (await self._session.execute(latest_rate_limited)).scalar_one_or_none()
        if rate_limited_at is None:
            return None

        latest_ok = select(func.max(FundamentalFetchLog.requested_at)).where(
            FundamentalFetchLog.status.in_([FetchStatus.SUCCESS.value, FetchStatus.CACHED.value])
        )
        ok_at = (await self._session.execute(latest_ok)).scalar_one_or_none()
        if ok_at is not None and ok_at > rate_limited_at:
            return None

        if rate_limited_at.tzinfo is None:
            rate_limited_at = rate_limited_at.replace(tzinfo=UTC)
        return rate_limited_at

    async def count_consecutive_rate_limits(self) -> int:
        """How many RATE_LIMITED entries have piled up since the most
        recent SUCCESS/CACHED (or from the very first log entry if
        there's never been one) — used to escalate the cooldown when
        Trendlyne's account-level quota stays exhausted across multiple
        attempts, instead of retrying at a fixed interval forever."""
        latest_ok = select(func.max(FundamentalFetchLog.requested_at)).where(
            FundamentalFetchLog.status.in_([FetchStatus.SUCCESS.value, FetchStatus.CACHED.value])
        )
        ok_at = (await self._session.execute(latest_ok)).scalar_one_or_none()

        stmt = (
            select(func.count())
            .select_from(FundamentalFetchLog)
            .where(FundamentalFetchLog.status == FetchStatus.RATE_LIMITED.value)
        )
        if ok_at is not None:
            stmt = stmt.where(FundamentalFetchLog.requested_at > ok_at)
        return (await self._session.execute(stmt)).scalar_one()
