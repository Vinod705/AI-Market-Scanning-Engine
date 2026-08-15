"""Persistence for `fundamental_snapshots` — the cached-snapshot layer
between the Fundamental Queue's background sync and any reader (dashboard,
API, a future intraday-scanner read) that must never itself call a live
fundamental provider.
"""

from dataclasses import fields
from datetime import UTC, timedelta
from datetime import date as date_

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.core.time import utc_now
from app.fundamentals.models import FundamentalData
from app.fundamentals.queue_models import FetchStatus
from app.fundamentals.snapshot_models import CachedFundamentalSnapshot
from app.models.fundamental_snapshot import FundamentalSnapshot
from app.models.symbol import Symbol

# Every FundamentalData field except symbol/as_of (stored in their own
# columns) and field_snapshots (rich per-field provenance — not needed by
# this cache's simple value/source/timestamp/freshness/status contract;
# `MultiSourceFundamentalProvider`'s live merge already handles that).
_CACHED_FIELDS = [
    f.name for f in fields(FundamentalData) if f.name not in {"symbol", "as_of", "field_snapshots"}
]


def _to_cache_dict(data: FundamentalData) -> dict[str, object]:
    return {
        name: value
        for name in _CACHED_FIELDS
        if (value := getattr(data, name)) not in (None, [])
    }


def _from_cache_dict(
    symbol: str, as_of: date_ | None, raw: dict[str, object]
) -> FundamentalData:
    known = {name: raw[name] for name in _CACHED_FIELDS if name in raw}
    return FundamentalData(symbol=symbol, as_of=as_of, **known)  # type: ignore[arg-type]


class FundamentalSnapshotRepository:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def upsert(
        self,
        symbol_id: int,
        *,
        data: FundamentalData | None,
        source: str | None,
        status: FetchStatus,
        error_message: str | None,
    ) -> FundamentalSnapshot:
        """Records one fetch attempt. A failed/empty attempt (`data is
        None`) updates only `last_checked_at`/`status`/`error_message` —
        the last known-good `data`/`source`/`fetched_at` are left exactly
        as they were, so this row never claims fresher data than it
        actually has (see the model's own docstring for why)."""
        row = await self._get(symbol_id)
        if row is None:
            row = FundamentalSnapshot(symbol_id=symbol_id, status=status.value)
            self._session.add(row)

        now = utc_now()
        row.last_checked_at = now
        row.status = status.value
        row.error_message = error_message

        if data is not None:
            row.source = source
            row.data = _to_cache_dict(data)
            row.as_of = data.as_of
            row.fetched_at = now

        await self._session.flush()
        return row

    async def get_cached(self, symbol_id: int) -> CachedFundamentalSnapshot | None:
        """Pure DB read — never calls a fundamental provider, so a caller
        on the live intraday-scan path can use this without risking a
        wait on an external request (the actual guarantee, not just a
        naming convention: there is no provider reference anywhere in
        this method)."""
        row = await self._get(symbol_id)
        if row is None:
            return None

        data: FundamentalData | None = None
        if row.data is not None:
            symbol_row = await self._session.get(Symbol, symbol_id)
            symbol_name = symbol_row.symbol if symbol_row is not None else str(symbol_id)
            data = _from_cache_dict(symbol_name, row.as_of, dict(row.data))
        ttl = timedelta(minutes=self._settings.fundamental_cache_ttl_minutes)
        fetched_at = row.fetched_at
        # SQLite (this test suite's backend) doesn't actually enforce
        # tz-aware storage for DateTime(timezone=True) — a value written
        # tz-aware can come back naive. Same fix as
        # FundamentalFetchLogRepository.most_recent_rate_limit.
        if fetched_at is not None and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        is_fresh = fetched_at is not None and (utc_now() - fetched_at) < ttl

        last_checked_at = row.last_checked_at
        if last_checked_at.tzinfo is None:
            last_checked_at = last_checked_at.replace(tzinfo=UTC)

        return CachedFundamentalSnapshot(
            symbol_id=row.symbol_id,
            data=data,
            source=row.source,
            as_of=row.as_of,
            fetched_at=fetched_at,
            last_checked_at=last_checked_at,
            status=FetchStatus(row.status),
            error_message=row.error_message,
            is_fresh=is_fresh,
        )

    async def _get(self, symbol_id: int) -> FundamentalSnapshot | None:
        stmt = select(FundamentalSnapshot).where(FundamentalSnapshot.symbol_id == symbol_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
