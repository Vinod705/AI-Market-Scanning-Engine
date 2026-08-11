"""Domain types for the Fundamental Queue (Phase 7.x) — kept separate from
`app.fundamentals.models` (raw financial data types) since these describe
the *process* of fetching, not the data itself.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class FetchStatus(StrEnum):
    """One `FundamentalFetchLog` row's outcome. Also doubles as the queue
    stage shown on the dashboard for a symbol currently being processed."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    RATE_LIMITED = "RATE_LIMITED"
    FAILED = "FAILED"
    CACHED = "CACHED"


@dataclass
class QueueRunResult:
    """What one `FundamentalQueueService.run_queue()` invocation did —
    logged and used to decide whether to keep going or stop."""

    processed: int = 0
    succeeded: int = 0
    cached: int = 0
    failed: int = 0
    rate_limited: bool = False
    budget_exhausted: bool = False
    symbols: list[str] = field(default_factory=list)


@dataclass
class QueueStatusSummary:
    """The /health and dashboard queue-status snapshot. Never carries the
    Trendlyne MCP URL/token — only counts and timestamps."""

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
    trendlyne_status: str  # HEALTHY | RATE_LIMITED | UNAVAILABLE | NOT_CONFIGURED
