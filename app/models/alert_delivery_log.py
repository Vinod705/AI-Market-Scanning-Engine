"""Alert delivery-log ORM model — one row per notification-provider send attempt."""

from datetime import datetime as datetime_

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AlertDeliveryLog(Base):
    """One send attempt for one alert, against one notification provider.

    `response_metadata` must never contain secrets (access tokens, API
    keys) — only whatever the provider considers safe response data
    (message id, status, error code). See `app/notifications/whatsapp.py`.
    """

    __tablename__ = "alert_delivery_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    response_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"AlertDeliveryLog(alert_id={self.alert_id}, provider={self.provider!r}, "
            f"status={self.status!r}, attempt={self.attempt_number})"
        )
