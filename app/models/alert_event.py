"""Alert event-log ORM model — an append-only audit trail per alert."""

from datetime import datetime as datetime_

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AlertEvent(Base):
    """One lifecycle event for one alert (CREATED, QUEUED, SENT, FAILED,
    RETRYING, EXPIRED, INVALIDATED, SUPPRESSED, ...). Kept separate from
    `Alert.status` (the current state) so the full history survives status
    transitions — needed for restart recovery and delivery auditing."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timestamp: Mapped[datetime_] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Named `event_metadata`, not `metadata` — SQLAlchemy's declarative Base
    # reserves that attribute name for the class's own MetaData object.
    event_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)

    def __repr__(self) -> str:
        return f"AlertEvent(alert_id={self.alert_id}, event_type={self.event_type!r})"
