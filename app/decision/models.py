"""Decision engine domain types.

Plain dataclasses/enums the `app/decision/` package operates on — not ORM
models (those live in `app/models/`) and not API schemas (those live in
`app/schemas/`). Mirrors the split `app/scanner/models.py` uses for the
same reason: keep the engine's own vocabulary free of persistence/HTTP
concerns.
"""

from dataclasses import dataclass, field
from datetime import date as date_
from datetime import datetime
from enum import StrEnum

from app.core.time import now_market_time


class Decision(StrEnum):
    ALERT = "ALERT"
    WATCH = "WATCH"
    REJECT = "REJECT"


class Quality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"


def derive_signal_type(scanner_name: str) -> str:
    """`"breakout_v1"` -> `"BREAKOUT"` — a display-friendly signal category,
    derived rather than duplicated as separate config. Shared by
    `DecisionEngine` and `DecisionService` so both build candidates the
    same way."""
    return scanner_name.split("_v")[0].upper()


@dataclass
class DecisionCandidate:
    """One `ScannerResult` row, adapted into the decision engine's own input
    shape. `feature_snapshot` comes straight from `ScannerResult.feature_snapshot`
    — the same point-in-time capture the scanner itself scored — so the
    decision engine re-validates against exactly what the scanner saw rather
    than re-reading (possibly since-changed) live feature rows."""

    symbol: str
    scanner_name: str
    signal_type: str
    score: float
    scan_date: date_
    feature_snapshot: dict[str, object]


@dataclass
class RuleResult:
    """The outcome of one configurable decision rule."""

    rule_name: str
    status: RuleStatus
    actual_value: str | None
    required_value: str | None
    reason: str


@dataclass
class DecisionResult:
    """The Decision Engine's verdict for one scanner candidate."""

    symbol: str
    scanner_name: str
    signal_type: str
    decision: Decision
    score: float
    quality: Quality | None
    passed_rules: list[str] = field(default_factory=list)
    failed_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    feature_snapshot: dict[str, object] = field(default_factory=dict)
    # Confirmed bug, fixed: defaulted to bare `datetime.now()` (naive,
    # host-local) — `app.alerts.deduplicator` truncates this to a date as
    # its `signal_date` fallback, a real business date. Now IST — see
    # app.core.time's module docstring.
    timestamp: datetime = field(default_factory=now_market_time)
    rule_results: list[RuleResult] = field(default_factory=list)
