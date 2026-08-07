"""Scanner event-log ORM model — per-symbol detail behind a scanner_runs summary row."""

from datetime import datetime as datetime_

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ScannerLog(Base):
    """A single rejection/error event during a scanner run.

    `scanner_runs` has the aggregate counts a status endpoint wants;
    this table has the "why" for a specific symbol when someone needs to
    debug a run — one row per rejection or error, not one per symbol scanned
    (qualifying/clean-pass symbols don't need a log line, they have their
    own `scanner_results` row already).
    """

    __tablename__ = "scanner_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("scanner_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), index=True, nullable=True
    )
    scanner_name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)  # "info" | "warning" | "error"
    message: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"ScannerLog(run_id={self.run_id}, level={self.level!r}, message={self.message[:40]!r})"
        )
