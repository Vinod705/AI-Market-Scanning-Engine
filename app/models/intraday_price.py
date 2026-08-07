"""Intraday OHLCV price ORM model."""

from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class IntradayPrice(Base):
    """One intraday OHLCV bar for a symbol."""

    __tablename__ = "intraday_prices"
    __table_args__ = (
        UniqueConstraint("symbol_id", "datetime", name="uq_intraday_prices_symbol_datetime"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    datetime: Mapped[datetime_] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    open: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"IntradayPrice(symbol_id={self.symbol_id}, datetime={self.datetime})"
