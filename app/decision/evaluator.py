"""DecisionEvaluator: aggregates Decision Rules v1 into one `DecisionResult`.

Decision logic:

- `required_features` or `data_freshness` failing means the candidate can't
  be trusted at all -> REJECT, regardless of anything else.
- Otherwise, every "core" rule (minimum score, trend, relative volume, ADX,
  resistance proximity) must PASS *and* the market must be open for the
  candidate to become an ALERT. `market_session` never fails a candidate on
  its own — outside market hours it downgrades what would've been an ALERT
  to a WATCH instead (see the spec: "Live alerts should only be generated
  during configured market hours", not "reject everything after close").
- Anything that clears the data-quality gates but doesn't fully qualify for
  ALERT becomes WATCH.
- Quality is only meaningful for ALERT (HIGH/MEDIUM by score band) and WATCH
  (LOW); REJECT carries no quality.
"""

from datetime import datetime

from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.models import (
    Decision,
    DecisionCandidate,
    DecisionResult,
    Quality,
    RuleResult,
    RuleStatus,
)
from app.decision.rules import RULES

_DATA_QUALITY_RULES = {"required_features", "data_freshness"}
_CORE_ALERT_RULES = {"minimum_score", "trend", "relative_volume", "adx", "resistance_proximity"}


class DecisionEvaluator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(
        self, candidate: DecisionCandidate, *, now: datetime | None = None
    ) -> DecisionResult:
        rule_results: list[RuleResult] = [
            rule(candidate, self._settings, now=now) for rule in RULES
        ]

        passed = [r.rule_name for r in rule_results if r.status == RuleStatus.PASS]
        failed = [r.rule_name for r in rule_results if r.status == RuleStatus.FAIL]
        warnings = [r.rule_name for r in rule_results if r.status == RuleStatus.WARNING]

        data_quality_failed = any(name in failed for name in _DATA_QUALITY_RULES)
        core_rules_passed = _CORE_ALERT_RULES.issubset(set(passed))
        market_open = "market_session" not in warnings

        if data_quality_failed:
            decision = Decision.REJECT
            quality = None
        elif core_rules_passed and market_open:
            decision = Decision.ALERT
            quality = (
                Quality.HIGH
                if candidate.score >= self._settings.alert_high_priority_score
                else Quality.MEDIUM
            )
        else:
            decision = Decision.WATCH
            quality = Quality.LOW

        return DecisionResult(
            symbol=candidate.symbol,
            scanner_name=candidate.scanner_name,
            signal_type=candidate.signal_type,
            decision=decision,
            score=candidate.score,
            quality=quality,
            passed_rules=passed,
            failed_rules=failed,
            warnings=warnings,
            feature_snapshot=candidate.feature_snapshot,
            timestamp=now or utc_now(),
            rule_results=rule_results,
        )
