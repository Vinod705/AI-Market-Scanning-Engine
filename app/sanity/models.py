"""Data shapes for the Project Sanity Check system.

Deliberately separate from `app.health.freshness` (which the trading
pipeline itself consumes, via `/health` and `ScannerEngine`) — this
module is the broader, standalone diagnostic surface: every worker,
infra dependency, and the full market-data -> daily_prices -> features
-> scanner_results chain, not just the one feature-freshness gap that
motivated `app.health.freshness` originally. `SanityService` (see
`service.py`) reuses `check_feature_freshness` rather than duplicating
its logic.
"""

from dataclasses import dataclass, field
from datetime import date as date_
from datetime import datetime as datetime_
from enum import StrEnum

from app.core.time import to_market_time


class SanityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNKNOWN = "UNKNOWN"


# Worse-to-better isn't the ordering that matters here — what matters is
# "which status wins when combining several components into one overall
# verdict". FAILED is the worst outcome a check can report on its own;
# NOT_CONFIGURED/UNKNOWN are informational, not failures, and never
# drag an otherwise-healthy system down to DEGRADED by themselves.
_SEVERITY_RANK: dict[SanityStatus, int] = {
    SanityStatus.HEALTHY: 0,
    SanityStatus.NOT_CONFIGURED: 0,
    SanityStatus.UNKNOWN: 1,
    SanityStatus.WARNING: 2,
    SanityStatus.STALE: 3,
    SanityStatus.BLOCKED: 3,
    SanityStatus.FAILED: 4,
}


def worst_status(statuses: list[SanityStatus]) -> SanityStatus:
    if not statuses:
        return SanityStatus.UNKNOWN
    return max(statuses, key=lambda s: _SEVERITY_RANK[s])


@dataclass
class ComponentCheck:
    name: str
    status: SanityStatus
    detail: str
    last_success_at: datetime_ | None = None


@dataclass
class ScannerCheck:
    scanner_name: str
    status: SanityStatus
    detail: str
    last_run_at: datetime_ | None = None
    latest_result_date: date_ | None = None


@dataclass
class SanityReport:
    checked_at: datetime_
    overall_status: SanityStatus
    components: list[ComponentCheck] = field(default_factory=list)
    scanners: list[ScannerCheck] = field(default_factory=list)
    daily_prices_latest_date: date_ | None = None
    daily_features_latest_date: date_ | None = None
    issues: list[str] = field(default_factory=list)
    # IST business-time context (section 14 of this project's IST audit):
    # `checked_at` above stays UTC (a technical instant); these are the
    # explicit Indian-market-timezone view of "now" for the dashboard/CLI
    # to display, per this project's rule that business-facing time is
    # always shown in IST, not UTC.
    market_timezone: str = ""
    checked_at_ist: datetime_ | None = None
    current_market_date: date_ | None = None

    def to_dict(self) -> dict[str, object]:
        tz = self.market_timezone or "Asia/Kolkata"

        def _ist(moment: datetime_ | None) -> str | None:
            # Business-facing display is always IST — `checked_at`/
            # `last_success_at`/`last_run_at` stay UTC in the raw fields
            # above/below (technical instants) purely for debugging.
            return to_market_time(moment, tz).isoformat() if moment else None

        return {
            "checked_at": self.checked_at.isoformat(),
            "checked_at_ist": self.checked_at_ist.isoformat() if self.checked_at_ist else None,
            "market_timezone": self.market_timezone,
            "current_market_date": self.current_market_date.isoformat()
            if self.current_market_date
            else None,
            "overall_status": self.overall_status.value,
            "components": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "detail": c.detail,
                    "last_success_at": c.last_success_at.isoformat()
                    if c.last_success_at
                    else None,
                    "last_success_at_ist": _ist(c.last_success_at),
                }
                for c in self.components
            ],
            "scanners": [
                {
                    "scanner_name": s.scanner_name,
                    "status": s.status.value,
                    "detail": s.detail,
                    "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
                    "last_run_at_ist": _ist(s.last_run_at),
                    "latest_result_date": s.latest_result_date.isoformat()
                    if s.latest_result_date
                    else None,
                }
                for s in self.scanners
            ],
            "daily_prices_latest_date": self.daily_prices_latest_date.isoformat()
            if self.daily_prices_latest_date
            else None,
            "daily_features_latest_date": self.daily_features_latest_date.isoformat()
            if self.daily_features_latest_date
            else None,
            "issues": self.issues,
        }
