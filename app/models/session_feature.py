"""Intraday session feature-store ORM model.

One row per (symbol, trading day), continuously updated through the session
as new intraday candles arrive (unlike `daily_features`, which is written
once per closed daily bar).
"""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SessionFeature(Base):
    """Intraday session-level features for one symbol on one trading day."""

    __tablename__ = "session_features"
    __table_args__ = (
        UniqueConstraint("symbol_id", "date", name="uq_session_features_symbol_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)

    opening_range_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    opening_range_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    initial_balance_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    initial_balance_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    day_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    day_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    prev_day_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    prev_day_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    session_vwap: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"SessionFeature(symbol_id={self.symbol_id}, date={self.date})"
