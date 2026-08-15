"""Persistence for `oi_observations`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.derivatives.derivatives_models import OiReading
from app.models.oi_observation import OiObservation


class OiObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, symbol_id: int, reading: OiReading) -> OiObservation:
        row = OiObservation(
            symbol_id=symbol_id,
            instrument_key=reading.instrument_key,
            instrument_type=reading.instrument_type.value,
            strike_price=reading.strike_price,
            expiry_date=reading.expiry_date,
            observed_at=reading.observed_at,
            price=reading.price,
            prev_price=reading.prev_price,
            price_change_pct=reading.price_change_pct,
            volume=reading.volume,
            oi=reading.oi,
            prev_oi=reading.prev_oi,
            oi_change=reading.oi_change,
            oi_change_pct=reading.oi_change_pct,
            classification=reading.classification.value,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_latest_for_instrument(self, instrument_key: str) -> OiObservation | None:
        stmt = (
            select(OiObservation)
            .where(OiObservation.instrument_key == instrument_key)
            .order_by(OiObservation.observed_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_symbol(self, symbol_id: int, limit: int = 100) -> list[OiObservation]:
        stmt = (
            select(OiObservation)
            .where(OiObservation.symbol_id == symbol_id)
            .order_by(OiObservation.observed_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
