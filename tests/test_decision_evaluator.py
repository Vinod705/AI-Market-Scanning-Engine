"""Tests for app.decision.evaluator.DecisionEvaluator — full decision logic."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.decision.evaluator import DecisionEvaluator
from app.decision.models import Decision, DecisionCandidate, Quality

_GOOD_SNAPSHOT: dict[str, object] = {
    "price": "110",
    "ema20": "105",
    "ema50": "100",
    "ema200": "90",
    "adx14": "30",
    "relative_volume": "2.5",
    "resistance_level": "112",
    "breakout_level": "112",
}

_MARKET_OPEN = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # Monday
_MARKET_CLOSED = datetime(2026, 1, 5, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _candidate(
    *, score: float = 95.0, scan_date: date = date(2026, 1, 5), **overrides: object
) -> DecisionCandidate:
    snapshot = {**_GOOD_SNAPSHOT, **overrides}
    return DecisionCandidate(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        score=score,
        scan_date=scan_date,
        feature_snapshot=snapshot,
    )


def test_full_pass_during_market_hours_is_alert_with_high_quality() -> None:
    settings = Settings(decision_min_alert_score=80.0, alert_high_priority_score=90.0)
    evaluator = DecisionEvaluator(settings)
    result = evaluator.evaluate(_candidate(score=95.0), now=_MARKET_OPEN)

    assert result.decision == Decision.ALERT
    assert result.quality == Quality.HIGH
    assert "minimum_score" in result.passed_rules
    assert not result.failed_rules


def test_medium_quality_between_min_and_high_threshold() -> None:
    settings = Settings(decision_min_alert_score=80.0, alert_high_priority_score=90.0)
    evaluator = DecisionEvaluator(settings)
    result = evaluator.evaluate(_candidate(score=85.0), now=_MARKET_OPEN)

    assert result.decision == Decision.ALERT
    assert result.quality == Quality.MEDIUM


def test_low_score_becomes_watch_not_reject() -> None:
    settings = Settings(decision_min_alert_score=80.0)
    evaluator = DecisionEvaluator(settings)
    result = evaluator.evaluate(_candidate(score=50.0), now=_MARKET_OPEN)

    assert result.decision == Decision.WATCH
    assert result.quality == Quality.LOW
    assert "minimum_score" in result.failed_rules


def test_market_closed_downgrades_alert_to_watch() -> None:
    settings = Settings(decision_min_alert_score=80.0)
    evaluator = DecisionEvaluator(settings)
    result = evaluator.evaluate(_candidate(score=95.0), now=_MARKET_CLOSED)

    assert result.decision == Decision.WATCH
    assert "market_session" in result.warnings


def test_missing_required_features_is_reject() -> None:
    evaluator = DecisionEvaluator(Settings())
    result = evaluator.evaluate(_candidate(ema200=None), now=_MARKET_OPEN)

    assert result.decision == Decision.REJECT
    assert result.quality is None
    assert "required_features" in result.failed_rules


def test_stale_data_is_reject() -> None:
    settings = Settings(decision_max_data_age_days=1)
    evaluator = DecisionEvaluator(settings)
    result = evaluator.evaluate(_candidate(scan_date=date(2020, 1, 1)), now=_MARKET_OPEN)

    assert result.decision == Decision.REJECT
    assert "data_freshness" in result.failed_rules


def test_failed_trend_rule_becomes_watch() -> None:
    evaluator = DecisionEvaluator(Settings())
    result = evaluator.evaluate(_candidate(score=95.0, ema50="106"), now=_MARKET_OPEN)

    assert result.decision == Decision.WATCH
    assert "trend" in result.failed_rules


def test_score_is_not_labeled_a_probability() -> None:
    """The spec is explicit: score is a 0-100 composite, never framed as a
    probability. This is a documentation-intent test, not a numeric one —
    it just asserts the field name/type stays score, not probability."""
    evaluator = DecisionEvaluator(Settings())
    result = evaluator.evaluate(_candidate(score=91.0), now=_MARKET_OPEN)

    assert result.score == 91.0
    assert not hasattr(result, "probability")
