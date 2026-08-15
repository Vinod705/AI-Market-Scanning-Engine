"""Persistence for `oi_observations`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.derivatives.derivatives_models import OiReading
from app.models.oi_observation import OiObservation

# Same 4-way classification `SignalFusionEngine._score_oi` already reads
# (see app/signals/signal_fusion_engine.py) — "buildup" activity broadly,
# not just the two literal *_BUILDUP labels, since a dashboard reader
# wants to see covering/unwinding moves too, not only fresh positions.
_BUILDUP_CLASSIFICATIONS = ("LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING")


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

    async def list_latest_buildups(self, limit: int = 20) -> list[OiObservation]:
        """The latest futures OI reading per symbol, restricted to the
        4-way buildup/covering/unwinding classification (never NEUTRAL),
        ranked by the size of the OI move — a read-only, already-computed
        view for the dashboard's "OI Buildup" section; classification
        itself was decided once, at ingest time (see
        `app.derivatives`), never recomputed here."""
        row_number = (
            func.row_number()
            .over(partition_by=OiObservation.symbol_id, order_by=OiObservation.observed_at.desc())
            .label("rn")
        )
        ranked = (
            select(OiObservation.id, row_number)
            .where(OiObservation.instrument_type == "FUT")
            .subquery()
        )
        stmt = (
            select(OiObservation)
            .join(ranked, OiObservation.id == ranked.c.id)
            .where(
                ranked.c.rn == 1,
                OiObservation.classification.in_(_BUILDUP_CLASSIFICATIONS),
            )
            .order_by(func.abs(OiObservation.oi_change_pct).desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
