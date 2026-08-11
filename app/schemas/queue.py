"""Pydantic schema for the Fundamental Queue's admin diagnostics endpoint."""

from datetime import datetime

from pydantic import BaseModel


class FundamentalQueueStatusOut(BaseModel):
    pending: int
    completed_today: int
    rate_limited_today: int
    failed_today: int
    cached_today: int
    batch_size: int
    is_paused: bool
    paused_until: datetime | None
    last_request_at: datetime | None
    requests_today: int
    daily_budget: int
    trendlyne_status: str
