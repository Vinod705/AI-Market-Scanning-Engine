"""Append-only market regime snapshot log — one row per scheduled
computation of `app.analytics.market.regime.compute_market_regime` (see
`app.scheduler.analytics_snapshot_jobs`). Exists so the read-only
dashboard/digest can show "current market regime" without ever
triggering that computation itself on a request.
"""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MarketRegimeSnapshot(Base):
    __tablename__ = "market_regime_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    computed_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    as_of: Mapped[date_] = mapped_column(Date, nullable=False)

    regime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    index_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    index_trend_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    index_trend_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    volatility_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sector_leading_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    evidence_sources_used: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"MarketRegimeSnapshot(computed_at={self.computed_at}, regime={self.regime!r})"
