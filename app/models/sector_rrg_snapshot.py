"""Append-only sector RRG/rotation snapshot log — one row per (sector
symbol, scheduled computation) of
`app.analytics.sector.sector_strength.compute_sector_evidence` (see
`app.scheduler.analytics_snapshot_jobs`). Covers both the "Sector
Rotation" and "RRG" dashboard sections: `rs_ratio`/`rs_momentum`/
`rotation_state` on one `SectorEvidence` reading IS this project's RRG
state for that sector (see `sector_strength.py`'s own docstring) — there
is no separate RRG data source to persist twice.
"""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SectorRrgSnapshot(Base):
    __tablename__ = "sector_rrg_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    computed_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    sector_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    benchmark_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False)

    rs_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    rs_momentum: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    rotation_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    trend_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    price_performance_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"SectorRrgSnapshot(sector_symbol={self.sector_symbol!r}, "
            f"rotation_state={self.rotation_state!r})"
        )
