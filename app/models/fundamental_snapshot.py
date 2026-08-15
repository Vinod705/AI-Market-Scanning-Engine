"""Cached fundamental snapshot — one row per symbol, the "cached snapshot"
stage of Fundamental provider -> background sync -> PostgreSQL -> cached
snapshot -> intraday scanner.

Distinct from `fundamental_fetch_log` (a full history of every attempt):
this table holds only the *current* state per symbol — the last known-good
`data`/`fetched_at`, plus `last_checked_at`/`status`/`error_message` for
the most recent attempt regardless of outcome. A failed attempt updates
`last_checked_at`/`status`/`error_message` but never touches `data`/
`fetched_at` — the record always honestly distinguishes "here's the last
data we actually got" from "here's whether we could refresh it just now",
so a stale value is never presented as if it were just confirmed current.
"""

from datetime import date as date_
from datetime import datetime as datetime_

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Last known-good data — untouched by a failed fetch attempt.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    as_of: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Most recent attempt — updated on every call, success or failure.
    last_checked_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"FundamentalSnapshot(symbol_id={self.symbol_id}, status={self.status!r}, "
            f"fetched_at={self.fetched_at})"
        )
