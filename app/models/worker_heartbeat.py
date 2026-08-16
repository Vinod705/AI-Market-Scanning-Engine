"""Worker heartbeat: durable, cross-process "did this worker complete a
cycle recently" signal for the Project Sanity Check system
(`app.sanity`).

Deliberately not an in-memory/per-process store: the sanity checker is
explicitly a separate application (CLI, its own API/dashboard) that must
be able to observe the main app's worker health without sharing a
process — so this has to live in the database, one row per worker,
updated in place. `last_run_at` moves on every cycle attempt (including
a legitimate no-op, e.g. market closed); `last_success_at` only moves
when the cycle actually completed without raising — the distinction the
sanity checker needs to tell "alive but genuinely nothing to do" apart
from "stopped working"."""

from datetime import datetime as datetime_

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_run_at: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime_ | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"WorkerHeartbeat(worker_name={self.worker_name!r}, last_success_at={self.last_success_at})"
