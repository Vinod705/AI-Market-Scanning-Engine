"""Persistence for `momentum_alert_observations` — Phase 16's live
paper/simulation-mode operational-validation record."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.momentum_alert_observation import MomentumAlertObservation


@dataclass
class NewObservation:
    transition_id: int
    symbol_id: int
    alert_id: int | None
    momentum_state: str
    trigger_at: datetime
    signal_score: Decimal
    signal_confidence: Decimal | None
    as_of_data_at: datetime | None
    data_age_seconds: float | None
    is_stale: bool
    price_at_trigger: Decimal | None


class MomentumAlertObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, data: NewObservation) -> MomentumAlertObservation:
        row = MomentumAlertObservation(
            transition_id=data.transition_id,
            symbol_id=data.symbol_id,
            alert_id=data.alert_id,
            momentum_state=data.momentum_state,
            trigger_at=data.trigger_at,
            signal_score=data.signal_score,
            signal_confidence=data.signal_confidence,
            as_of_data_at=data.as_of_data_at,
            data_age_seconds=data.data_age_seconds,
            is_stale=data.is_stale,
            price_at_trigger=data.price_at_trigger,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_recent(self, limit: int = 50) -> list[MomentumAlertObservation]:
        stmt = (
            select(MomentumAlertObservation)
            .order_by(MomentumAlertObservation.trigger_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_awaiting_15m(self, cutoff: datetime, limit: int = 200) -> list[MomentumAlertObservation]:
        """Observations whose trigger is at least 15 minutes old but
        haven't had that horizon's real subsequent price recorded yet."""
        stmt = (
            select(MomentumAlertObservation)
            .where(
                MomentumAlertObservation.trigger_at <= cutoff,
                MomentumAlertObservation.price_after_15m.is_(None),
            )
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_awaiting_1h(self, cutoff: datetime, limit: int = 200) -> list[MomentumAlertObservation]:
        stmt = (
            select(MomentumAlertObservation)
            .where(
                MomentumAlertObservation.trigger_at <= cutoff,
                MomentumAlertObservation.price_after_1h.is_(None),
            )
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_awaiting_1d(self, cutoff: datetime, limit: int = 200) -> list[MomentumAlertObservation]:
        stmt = (
            select(MomentumAlertObservation)
            .where(
                MomentumAlertObservation.trigger_at <= cutoff,
                MomentumAlertObservation.price_after_1d.is_(None),
            )
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
