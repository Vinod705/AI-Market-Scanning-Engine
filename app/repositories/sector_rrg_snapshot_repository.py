"""Persistence for `sector_rrg_snapshots` — the read-only cache the
dashboard/digest consume instead of ever calling
`app.analytics.sector.sector_rotation.compute_sector_rotation` directly.
Covers both the "Sector Rotation" and "RRG" dashboard sections (see the
model's own docstring for why one table serves both)."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sector.sector_models import SectorEvidence
from app.models.sector_rrg_snapshot import SectorRrgSnapshot


class SectorRrgSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_many(
        self, evidences: list[SectorEvidence], *, computed_at: datetime
    ) -> None:
        for evidence in evidences:
            self._session.add(
                SectorRrgSnapshot(
                    computed_at=computed_at,
                    sector_symbol=evidence.sector_symbol,
                    benchmark_symbol=evidence.benchmark_symbol,
                    date=evidence.date,
                    rs_ratio=evidence.rs_ratio,
                    rs_momentum=evidence.rs_momentum,
                    rotation_state=(
                        evidence.rotation_state.value
                        if evidence.rotation_state is not None
                        else None
                    ),
                    momentum_score=evidence.momentum_score,
                    trend_strength=evidence.trend_strength,
                    price_performance_pct=evidence.price_performance_pct,
                    score=evidence.score,
                )
            )
        await self._session.flush()

    async def get_latest_batch(self) -> list[SectorRrgSnapshot]:
        """The most recent snapshot row per `sector_symbol` — same
        "latest per group" window-function idiom as
        `DailyFeatureRepository.get_latest_bulk`, just partitioned by
        sector symbol instead of `symbol_id`."""
        row_number = (
            func.row_number()
            .over(
                partition_by=SectorRrgSnapshot.sector_symbol,
                order_by=SectorRrgSnapshot.computed_at.desc(),
            )
            .label("rn")
        )
        ranked = select(SectorRrgSnapshot.id, row_number).subquery()
        stmt = (
            select(SectorRrgSnapshot)
            .join(ranked, SectorRrgSnapshot.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(SectorRrgSnapshot.sector_symbol)
        )
        return list((await self._session.execute(stmt)).scalars().all())
