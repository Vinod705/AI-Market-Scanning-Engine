"""Repository layer for the feature store: `daily_features` and `session_features`."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime as datetime_

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_feature import DailyFeature
from app.models.session_feature import SessionFeature

# Columns every caller may set via `upsert(**values)`; `values` comes from a
# pandas row turned into a dict, so this also guards against accidentally
# writing an unexpected column if a calculator's output ever drifts from the
# model's schema.
_DAILY_FEATURE_COLUMNS = {c.name for c in DailyFeature.__table__.columns} - {
    "id",
    "symbol_id",
    "date",
    "created_at",
    "updated_at",
}
_SESSION_FEATURE_COLUMNS = {c.name for c in SessionFeature.__table__.columns} - {
    "id",
    "symbol_id",
    "date",
    "created_at",
    "updated_at",
}


@dataclass
class FeatureStatusSummary:
    symbols_with_features: int
    total_daily_feature_rows: int
    last_computed_at: datetime_ | None


class DailyFeatureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_date(self, symbol_id: int) -> date_ | None:
        stmt = (
            select(DailyFeature.date)
            .where(DailyFeature.symbol_id == symbol_id)
            .order_by(DailyFeature.date.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self, symbol_id: int, date: date_, values: Mapping[str, object]
    ) -> DailyFeature:
        stmt = select(DailyFeature).where(
            DailyFeature.symbol_id == symbol_id, DailyFeature.date == date
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = DailyFeature(symbol_id=symbol_id, date=date)
            self._session.add(row)

        for column, value in values.items():
            if column in _DAILY_FEATURE_COLUMNS:
                setattr(row, column, value)

        await self._session.flush()
        return row

    async def get_latest(self, symbol_id: int) -> DailyFeature | None:
        stmt = (
            select(DailyFeature)
            .where(DailyFeature.symbol_id == symbol_id)
            .order_by(DailyFeature.date.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_history(self, symbol_id: int, limit: int = 100) -> list[DailyFeature]:
        stmt = (
            select(DailyFeature)
            .where(DailyFeature.symbol_id == symbol_id)
            .order_by(DailyFeature.date.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(reversed(rows))

    async def get_status_summary(self) -> FeatureStatusSummary:
        stmt = select(
            func.count(func.distinct(DailyFeature.symbol_id)),
            func.count(DailyFeature.id),
            func.max(DailyFeature.updated_at),
        )
        symbols_with_features, total_rows, last_computed_at = (
            await self._session.execute(stmt)
        ).one()
        return FeatureStatusSummary(
            symbols_with_features=symbols_with_features or 0,
            total_daily_feature_rows=total_rows or 0,
            last_computed_at=last_computed_at,
        )


class SessionFeatureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, symbol_id: int, date: date_, values: Mapping[str, object]
    ) -> SessionFeature:
        stmt = select(SessionFeature).where(
            SessionFeature.symbol_id == symbol_id, SessionFeature.date == date
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = SessionFeature(symbol_id=symbol_id, date=date)
            self._session.add(row)

        for column, value in values.items():
            if column in _SESSION_FEATURE_COLUMNS:
                setattr(row, column, value)

        await self._session.flush()
        return row

    async def get_latest(self, symbol_id: int) -> SessionFeature | None:
        stmt = (
            select(SessionFeature)
            .where(SessionFeature.symbol_id == symbol_id)
            .order_by(SessionFeature.date.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
