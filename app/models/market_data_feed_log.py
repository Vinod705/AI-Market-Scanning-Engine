"""Upstox WebSocket market feed connection log ORM model."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MarketDataFeedLog(Base):
    """One record per connect/reconnect cycle of the Upstox WebSocket feed.

    Connection-level observability (uptime, throughput, dedup rate) —
    distinct from `collector_logs` (one row per REST collector run) and
    from `PipelineEvent.source` (which already distinguishes WS- vs
    REST-sourced candles at the per-batch level). Nothing here is written
    per-tick; one row is opened on connect and updated as the connection
    runs, closed on disconnect/reconnect.
    """

    __tablename__ = "market_data_feed_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    messages_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ticks_processed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    duplicates_dropped: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    candles_flushed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    disconnect_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"MarketDataFeedLog(connected_at={self.connected_at}, "
            f"ticks_processed={self.ticks_processed})"
        )
