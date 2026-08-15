"""Domain types for the momentum-state-aware decision layer (Phase 13).

Kept separate from `app.decision.models.Decision` (ALERT/WATCH/REJECT) —
that enum belongs to the existing, unmodified F&O/IPO candidate path
(`app.decision.evaluator.DecisionEvaluator`, still rule-based, still
scanner_result-driven, still exactly as it was). `PipelineVerdict` is the
momentum-state-driven verdict this new path produces instead, per this
phase's own naming.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.momentum.momentum_models import MomentumState


class PipelineVerdict(StrEnum):
    TRIGGER = "TRIGGER"
    WATCH = "WATCH"
    REJECT = "REJECT"
    INVALIDATE = "INVALIDATE"


@dataclass
class PipelineDecisionResult:
    """One symbol's verdict for one pipeline run. `alert_id` reflects
    whatever `app.momentum.momentum_engine.MomentumStateEngine` already
    did (it owns the one and only `AlertManager.process()` call in this
    path) — this dataclass never triggers a second alert, it only labels
    what already happened."""

    symbol: str
    verdict: PipelineVerdict
    reason: str
    momentum_state: MomentumState | None
    signal_score: float | None
    confidence: float
    alert_id: int | None
    timestamp: datetime
