"""Alert ORM model — one row per ALERT-grade decision, deduplicated by fingerprint."""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Alert(Base):
    """A single ALERT-grade decision, from creation through delivery.

    `fingerprint` is indexed but deliberately *not* DB-unique: it's the
    "no duplicate alert" key while a signal is active (see
    `app/alerts/deduplicator.py`, which checks for an existing non-expired
    row with the same fingerprint before creating a new one), but once an
    alert EXPIREs a materially-unchanged signal is allowed to raise a fresh
    one later the same day — which means the same fingerprint value can
    legitimately appear on more than one row over time.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scanner_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "ALERT" (only decision persisted)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)  # "HIGH" | "MEDIUM"

    # Reference values only — never invented entry/stop/target prices (see
    # app/alerts/formatter.py docstring for why).
    entry_reference: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    breakout_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    support_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    resistance_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The DecisionResult.passed_rules that made this an ALERT — stored
    # separately from `reason` (free text) so the notification message can
    # be rebuilt from the DB alone after a restart, without parsing text.
    passed_rules: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    # Delivery status — distinct from `decision`: this tracks the
    # notification lifecycle (PENDING/SENT/FAILED/RETRYING/EXPIRED).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    signal_date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"Alert(symbol_id={self.symbol_id}, fingerprint={self.fingerprint!r}, status={self.status!r})"
