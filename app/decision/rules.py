"""Decision Rules v1.

Each `check_*` function evaluates one configurable rule against a
`DecisionCandidate` and returns a structured `RuleResult` — never raises,
never short-circuits the others, so the evaluator always gets a complete
picture of what passed/failed/warned.

All thresholds come from `Settings` (`DECISION_*` / `MARKET_*` env vars) —
nothing here is hardcoded.
"""

from collections.abc import Callable
from datetime import datetime

from app.config.settings import Settings
from app.data.market_updater import MarketStatusUpdater
from app.decision.models import DecisionCandidate, RuleResult, RuleStatus
from app.decision.validator import DecisionValidator

REQUIRED_FEATURE_KEYS = [
    "price",
    "ema20",
    "ema50",
    "ema200",
    "adx14",
    "relative_volume",
    "resistance_level",
]


def check_required_features(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    missing = DecisionValidator.missing_features(candidate.feature_snapshot, REQUIRED_FEATURE_KEYS)
    if missing:
        return RuleResult(
            rule_name="required_features",
            status=RuleStatus.FAIL,
            actual_value=", ".join(missing),
            required_value="all present",
            reason=f"missing/invalid required feature(s): {', '.join(missing)}",
        )
    return RuleResult(
        rule_name="required_features",
        status=RuleStatus.PASS,
        actual_value="all present",
        required_value="all present",
        reason="all required features are present and valid",
    )


def check_data_freshness(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    today = now.date() if now is not None else None
    fresh = DecisionValidator.is_fresh(
        candidate.scan_date, max_age_days=settings.decision_max_data_age_days, today=today
    )
    return RuleResult(
        rule_name="data_freshness",
        status=RuleStatus.PASS if fresh else RuleStatus.FAIL,
        actual_value=candidate.scan_date.isoformat(),
        required_value=f"within {settings.decision_max_data_age_days} day(s) of today",
        reason=(
            "scanner result is current"
            if fresh
            else f"scanner result dated {candidate.scan_date.isoformat()} is stale"
        ),
    )


def check_minimum_score(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    passed = candidate.score >= settings.decision_min_alert_score
    return RuleResult(
        rule_name="minimum_score",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        actual_value=str(candidate.score),
        required_value=f">= {settings.decision_min_alert_score}",
        reason=(
            "scanner score meets the minimum alert threshold"
            if passed
            else "scanner score is below the minimum alert threshold"
        ),
    )


def check_trend(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    ema20 = DecisionValidator.as_float(candidate.feature_snapshot, "ema20")
    ema50 = DecisionValidator.as_float(candidate.feature_snapshot, "ema50")
    ema200 = DecisionValidator.as_float(candidate.feature_snapshot, "ema200")
    if ema20 is None or ema50 is None or ema200 is None:
        return RuleResult(
            rule_name="trend",
            status=RuleStatus.FAIL,
            actual_value=None,
            required_value="EMA20 > EMA50 > EMA200",
            reason="one or more EMA values are missing",
        )
    passed = ema20 > ema50 > ema200
    return RuleResult(
        rule_name="trend",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        actual_value=f"ema20={ema20}, ema50={ema50}, ema200={ema200}",
        required_value="EMA20 > EMA50 > EMA200",
        reason="trend is aligned (uptrend)" if passed else "EMA stack is not aligned",
    )


def check_relative_volume(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    rvol = DecisionValidator.as_float(candidate.feature_snapshot, "relative_volume")
    if rvol is None:
        return RuleResult(
            rule_name="relative_volume",
            status=RuleStatus.FAIL,
            actual_value=None,
            required_value=f">= {settings.decision_min_rvol}",
            reason="relative volume is missing",
        )
    passed = rvol >= settings.decision_min_rvol
    return RuleResult(
        rule_name="relative_volume",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        actual_value=str(rvol),
        required_value=f">= {settings.decision_min_rvol}",
        reason=(
            "relative volume confirms increased participation"
            if passed
            else "relative volume is below the required threshold"
        ),
    )


def check_adx(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    adx = DecisionValidator.as_float(candidate.feature_snapshot, "adx14")
    if adx is None:
        return RuleResult(
            rule_name="adx",
            status=RuleStatus.FAIL,
            actual_value=None,
            required_value=f">= {settings.decision_min_adx}",
            reason="ADX is missing",
        )
    passed = adx >= settings.decision_min_adx
    return RuleResult(
        rule_name="adx",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        actual_value=str(adx),
        required_value=f">= {settings.decision_min_adx}",
        reason="trend strength confirmed" if passed else "trend strength is too weak",
    )


def check_resistance_proximity(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    price = DecisionValidator.as_float(candidate.feature_snapshot, "price")
    resistance = DecisionValidator.as_float(candidate.feature_snapshot, "resistance_level")
    if price is None or resistance is None or resistance == 0:
        return RuleResult(
            rule_name="resistance_proximity",
            status=RuleStatus.FAIL,
            actual_value=None,
            required_value=f"<= {settings.decision_resistance_distance_percent}%",
            reason="price or resistance level is missing",
        )
    distance_pct = abs(price - resistance) / resistance * 100.0
    passed = distance_pct <= settings.decision_resistance_distance_percent
    return RuleResult(
        rule_name="resistance_proximity",
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        actual_value=f"{distance_pct:.2f}%",
        required_value=f"<= {settings.decision_resistance_distance_percent}%",
        reason=(
            "price is approaching the breakout/resistance level"
            if passed
            else "price is too far from the breakout/resistance level"
        ),
    )


def check_market_session(
    candidate: DecisionCandidate, settings: Settings, *, now: datetime | None = None
) -> RuleResult:
    open_now = MarketStatusUpdater.is_market_open(now)
    return RuleResult(
        rule_name="market_session",
        status=RuleStatus.PASS if open_now else RuleStatus.WARNING,
        actual_value="open" if open_now else "closed",
        required_value="open",
        reason=(
            "market is open — live alert eligible"
            if open_now
            else "market is closed — signal downgraded to WATCH"
        ),
    )


# Rules evaluated in this order; market_session is last since it only ever
# produces WARNING (never FAIL) — it caps ALERT to WATCH rather than
# rejecting the candidate outright (see DecisionEvaluator).
RULES: list[Callable[..., RuleResult]] = [
    check_required_features,
    check_data_freshness,
    check_minimum_score,
    check_trend,
    check_relative_volume,
    check_adx,
    check_resistance_proximity,
    check_market_session,
]
