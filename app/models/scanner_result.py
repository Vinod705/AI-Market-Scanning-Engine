"""Scanner result ORM model — one row per (symbol, scanner, trading day)."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ScannerResult(Base):
    """A scanner's verdict for one symbol on one trading day.

    Unique on (symbol_id, scanner_name, date): re-scanning the same symbol
    on the same day updates this row instead of inserting a new one, which
    is what "no duplicate alert already generated" means in practice here.
    """

    __tablename__ = "scanner_results"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id", "scanner_name", "date", name="uq_scanner_results_symbol_scanner_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scanner_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)

    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "qualified" | "rejected"
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"ScannerResult(symbol_id={self.symbol_id}, scanner={self.scanner_name!r}, date={self.date}, status={self.status!r})"
