"""Current momentum-state ORM model — one row per symbol, the "where is
this symbol right now" pointer. The full transition history lives in
`app.models.momentum_state_transition.MomentumStateTransition` (append-only);
this table is just the latest position, kept in sync on every transition.
"""

from datetime import datetime as datetime_

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MomentumStateRecord(Base):
    __tablename__ = "momentum_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    entered_at: Mapped[datetime_] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"MomentumState(symbol_id={self.symbol_id}, state={self.state!r})"
