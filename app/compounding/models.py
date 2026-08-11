"""Compounding/Opportunity layer domain types.

See `app.compounding.engine` module docstring for the full calculation
approach — these are plain dataclasses/enums, not ORM models (matches the
existing `app/candidates/models.py`, `app/decision/models.py` split) and
nothing here is persisted: every `CompoundingResult` is computed fresh at
read time from an already-persisted `feature_snapshot`, same pattern as
`app.decision.evaluator.DecisionEvaluator`.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class TargetClassification(StrEnum):
    """Potential-upside band — see `app.compounding.target.classify_upside`
    for the exact thresholds. `UNKNOWN` only when no target could be
    derived at all (never fabricated), distinct from `NOT_ATTRACTIVE`
    (a real target was computed, it's just <5%)."""

    NOT_ATTRACTIVE = "NOT_ATTRACTIVE"
    MINIMUM_OPPORTUNITY = "MINIMUM_OPPORTUNITY"
    GOOD = "GOOD"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"
    EXCEPTIONAL = "EXCEPTIONAL"
    UNKNOWN = "UNKNOWN"


class Timeframe(StrEnum):
    """The implied holding horizon for the move this setup targets —
    derived from target magnitude + higher-timeframe confirmation (see
    `app.compounding.engine._classify_timeframe`), never a separate
    weekly/monthly scan (this system has no weekly/monthly bar
    pipeline — see that function's docstring)."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    UNKNOWN = "UNKNOWN"


class HigherTimeframeConfirmation(StrEnum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class CompoundingDecision(StrEnum):
    """Deliberately not reusing `app.decision.models.Decision`
    (ALERT/WATCH/REJECT) — this is a distinct, additional assessment
    layered on top of (not replacing) the existing Decision Engine
    verdict. No "BUY" language: that terminology doesn't exist anywhere
    else in this application."""

    STRONG_CANDIDATE = "STRONG_CANDIDATE"
    TRADE_CANDIDATE = "TRADE_CANDIDATE"
    WATCH = "WATCH"
    NOT_ATTRACTIVE = "NOT_ATTRACTIVE"


@dataclass
class TargetResult:
    target_price: float | None
    potential_upside_pct: float | None
    target_classification: TargetClassification
    basis: str  # which levels/method produced this, or why it's UNKNOWN


@dataclass
class RiskResult:
    stop_loss: float | None
    risk_pct: float | None
    reward_risk_ratio: float | None
    basis: str


@dataclass
class CompoundingResult:
    timeframe: Timeframe
    setup_type: str | None
    current_price: float | None
    target_price: float | None
    potential_upside_pct: float | None
    stop_loss: float | None
    risk_pct: float | None
    reward_risk_ratio: float | None
    target_classification: TargetClassification
    higher_timeframe_confirmation: HigherTimeframeConfirmation
    fundamental_quality: str  # HIGH | MEDIUM | LOW | UNKNOWN
    compounding_score: float
    decision: CompoundingDecision
    reasons: list[str] = field(default_factory=list)
    data_limitations: list[str] = field(default_factory=list)
