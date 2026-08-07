"""Symbol master ORM model."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Symbol(Base):
    """A tradable instrument, sourced from a provider's symbol master."""

    __tablename__ = "symbols"
    __table_args__ = (UniqueConstraint("symbol", "exchange", name="uq_symbols_symbol_exchange"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_token: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_ipo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"Symbol(symbol={self.symbol!r}, exchange={self.exchange!r})"
