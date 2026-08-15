"""Append-only momentum-state transition log — one row per transition,
never mutated, never deleted. What makes the state machine auditable:
every move carries its own timestamp/reason/score/evidence, independent
of whatever the "current state" pointer (`app.models.momentum_state.MomentumState`)
says right now.
"""

from datetime import datetime as datetime_

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MomentumStateTransition(Base):
    __tablename__ = "momentum_state_transitions"
    __table_args__ = (
        Index("ix_momentum_state_transitions_symbol_ts", "symbol_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime_] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"MomentumStateTransition(symbol_id={self.symbol_id}, "
            f"{self.from_state!r} -> {self.to_state!r})"
        )
