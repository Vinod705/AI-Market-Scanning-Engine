"""Fundamental Queue's durable record: one row per Trendlyne fetch
*attempt* (not per symbol) — success, rate-limited, failed, or served
from cache. This is what makes the queue restart-safe (a fresh process
can compute "requests made today" and "when did we last hit a rate
limit" straight from this table, without any in-memory state) and is
also the data source for the /health and dashboard queue-status views.
"""

from datetime import datetime as datetime_

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FundamentalFetchLog(Base):
    __tablename__ = "fundamental_fetch_log"
    __table_args__ = (
        Index("ix_fundamental_fetch_log_requested_at", "requested_at"),
        Index("ix_fundamental_fetch_log_symbol_requested", "symbol_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    # SUCCESS | RATE_LIMITED | FAILED | CACHED — see app.fundamentals.queue_models.FetchStatus
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"FundamentalFetchLog(symbol_id={self.symbol_id}, status={self.status!r}, requested_at={self.requested_at})"
