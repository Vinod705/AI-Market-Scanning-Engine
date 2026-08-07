"""Market status singleton ORM model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

SINGLETON_ID = 1


class MarketStatus(Base):
    """Current, continuously-updated snapshot of the collection service's health.

    Single-row table (id is always `SINGLETON_ID`); historical run detail lives
    in `collector_logs` instead.
    """

    __tablename__ = "market_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"MarketStatus(market_open={self.market_open}, provider_connected={self.provider_connected})"
