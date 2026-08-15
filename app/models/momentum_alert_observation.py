"""Live paper/simulation-mode operational-validation record (Phase 16) —
one row per alert-worthy momentum-state transition the live pipeline
produced, tracking exactly what the trigger looked like at the moment it
fired and what price actually did afterward.

**Not a backtest, not a trade**: nothing here simulates an entry, exit,
P&L, or "was this correct" verdict — every `price_after_*`/`*_pct` field
is a plain factual observation of a real subsequent price, filled in
later from already-collected `intraday_prices`/`daily_prices` rows (see
`app.scheduler.momentum_observation_followup_jobs`), never estimated or
interpolated. A human reading the dashboard draws the "was this a good
trigger" conclusion; this table only supplies the facts.
"""

from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MomentumAlertObservation(Base):
    __tablename__ = "momentum_alert_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    transition_id: Mapped[int] = mapped_column(
        ForeignKey("momentum_state_transitions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )

    momentum_state: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    signal_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    signal_confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Freshness of the evidence the trigger actually fired on — captured
    # once, at trigger time, never recomputed against a later "now".
    as_of_data_at: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_age_seconds: Mapped[float | None] = mapped_column(nullable=True)
    is_stale: Mapped[bool] = mapped_column(nullable=False, default=False)

    price_at_trigger: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    # Filled in later, once real time has actually passed — see the
    # follow-up job. NULL means "not observable yet", never a guess.
    price_after_15m: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    price_after_1h: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    price_after_1d: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    price_change_pct_15m: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    price_change_pct_1h: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    price_change_pct_1d: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"MomentumAlertObservation(symbol_id={self.symbol_id}, "
            f"momentum_state={self.momentum_state!r}, trigger_at={self.trigger_at})"
        )
