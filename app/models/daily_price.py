"""Daily OHLCV price ORM model."""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DailyPrice(Base):
    """One end-of-day OHLCV bar for a symbol."""

    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("symbol_id", "date", name="uq_daily_prices_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)

    open: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"DailyPrice(symbol_id={self.symbol_id}, date={self.date})"
