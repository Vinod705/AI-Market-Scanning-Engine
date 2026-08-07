"""Scanner run-log ORM model — one row per scheduled execution of one scanner."""

from datetime import datetime as datetime_

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ScannerRun(Base):
    """One record per scanner run: when it ran, how many symbols, how it went."""

    __tablename__ = "scanner_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scanner_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    start_time: Mapped[datetime_] = mapped_column(DateTime(timezone=True), nullable=False)
    finish_time: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qualified_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ScannerRun(scanner={self.scanner_name!r}, qualified={self.qualified_count}, rejected={self.rejected_count})"
