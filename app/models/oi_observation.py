"""Timestamped open-interest observation ORM model — one row per (contract,
fetch), never overwritten in place, so a buildup classification always has
a real prior stored reading to compare against as more rows accumulate."""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OiObservation(Base):
    """One contract's OI reading at one point in time. `symbol_id` is the
    underlying equity `Symbol`, not the contract itself (contracts aren't
    stored as their own `Symbol` rows — see `app.derivatives`)."""

    __tablename__ = "oi_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    instrument_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instrument_type: Mapped[str] = mapped_column(String(8), nullable=False)  # FUT | CE | PE
    strike_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    expiry_date: Mapped[date_] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    price: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    prev_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    price_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    oi: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    prev_oi: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    oi_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    oi_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    classification: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"OiObservation(symbol_id={self.symbol_id}, instrument_key={self.instrument_key}, "
            f"classification={self.classification})"
        )
