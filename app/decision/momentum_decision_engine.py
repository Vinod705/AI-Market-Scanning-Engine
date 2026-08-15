"""MomentumDecisionEngine: labels a TRIGGER/WATCH/REJECT/INVALIDATE
verdict from already-computed inputs — pure function, no I/O, no network
call of any kind.

**"Do not call external news/fundamental APIs synchronously" is true by
construction here**: `decide()` takes plain values (a confidence
percentage, a `MomentumState`) as parameters. There is nothing in this
module capable of reaching a provider even if it wanted to — the actual
guarantee comes from *what the caller passes in*
(`app.decision.momentum_pipeline_coordinator`), which computes
`SignalFusionResult` without a news provider injected (so News is
honestly MISSING, never a live call) and reads fundamentals only from
`FundamentalSnapshotRepository`'s cache (never the live Upstox/Trendlyne
provider) — see that module's docstring.

**Verdict mapping** — deliberately reuses two things that already exist
rather than inventing new rules:
- REJECT: confidence below `settings.pipeline_min_confidence_pct` — same
  "reject on data quality before trusting anything else" ordering
  `DecisionEvaluator` already uses (`data_quality_failed` is checked
  first, before any rule about the score itself).
- TRIGGER/WATCH/INVALIDATE: a direct, total mapping from the momentum
  state the state machine already landed on
  (`app.momentum.momentum_models.ALERT_WORTHY_STATES` for TRIGGER;
  EXHAUSTED/INVALIDATED both map to INVALIDATE — from a "should anything
  further happen" standpoint they're the same answer, whether the setup
  never triggered or triggered and faded; everything else pre-trigger
  is WATCH).
"""

from app.config.settings import Settings
from app.decision.momentum_decision_models import PipelineVerdict
from app.momentum.momentum_models import ALERT_WORTHY_STATES, MomentumState

_INVALIDATED_STATES = frozenset({MomentumState.INVALIDATED, MomentumState.EXHAUSTED})


class MomentumDecisionEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def decide(
        self, *, confidence: float, momentum_state: MomentumState | None
    ) -> tuple[PipelineVerdict, str]:
        min_confidence = self._settings.pipeline_min_confidence_pct
        if confidence < min_confidence:
            return (
                PipelineVerdict.REJECT,
                f"confidence={confidence:.2f}% below minimum ({min_confidence:.2f}%)",
            )

        if momentum_state is None:
            return PipelineVerdict.REJECT, "no momentum state available"

        if momentum_state in ALERT_WORTHY_STATES:
            return PipelineVerdict.TRIGGER, f"momentum state is {momentum_state.value}"

        if momentum_state in _INVALIDATED_STATES:
            return PipelineVerdict.INVALIDATE, f"momentum state is {momentum_state.value}"

        return PipelineVerdict.WATCH, f"momentum state is {momentum_state.value}"
