"""Tests for app.candidates.explainer — category rollups and the
deterministic WHY/decision narrative."""

from app.candidates.explainer import (
    explain_decision,
    fundamental_breakdown,
    rollup_by_category,
    technical_breakdown,
)
from app.config.settings import Settings


def _factor(category: str, contribution: float, status: str = "POSITIVE") -> dict[str, object]:
    return {
        "factor_name": f"{category}_factor",
        "category": category,
        "value": "1",
        "normalized_score": 80.0,
        "weight": 0.1,
        "contribution": contribution,
        "status": status,
        "reason": f"{category} favorable",
    }


def test_rollup_groups_factors_by_category_and_sums_contribution() -> None:
    factors = [_factor("trend", 10.0), _factor("trend", 5.0), _factor("momentum", 3.0)]
    weights = {"trend": 0.25, "momentum": 0.20}
    labels = {"trend": "Trend", "momentum": "Momentum"}

    result = rollup_by_category(factors, weights, labels)

    trend = next(b for b in result if b.category == "trend")
    assert trend.score == 15.0
    assert trend.max_score == 25.0
    assert len(trend.factors) == 2

    momentum = next(b for b in result if b.category == "momentum")
    assert momentum.score == 3.0
    assert momentum.max_score == 20.0


def test_rollup_includes_categories_with_no_known_factors() -> None:
    """A category nobody scored yet still appears with score 0, not
    silently omitted — "no black box" means showing what's missing too."""
    result = rollup_by_category([], {"vwap": 0.15}, {"vwap": "VWAP"})
    assert len(result) == 1
    assert result[0].score == 0.0
    assert result[0].max_score == 15.0
    assert result[0].factors == []


def test_technical_breakdown_uses_settings_weights() -> None:
    settings = Settings()
    factors = [_factor("trend", 12.5)]
    result = technical_breakdown(factors, settings)
    trend = next(b for b in result if b.category == "trend")
    assert trend.max_score == round(settings.technical_weight_trend * 100, 2)
    assert trend.label == "Trend"


def test_fundamental_breakdown_uses_settings_weights() -> None:
    settings = Settings()
    factors = [_factor("growth", 8.0)]
    result = fundamental_breakdown(factors, settings)
    growth = next(b for b in result if b.category == "growth")
    assert growth.max_score == round(settings.fundamental_weight_growth * 100, 2)


def test_explain_decision_reports_unknown_fundamentals_honestly() -> None:
    explanation = explain_decision(
        fundamental_score=None,
        technical_reasons=["Trend direction favorable"],
        fundamental_reasons=[],
        setup_state="MOMENTUM",
        passed_rules=["adx>=threshold"],
        failed_rules=[],
        risk_flags=["Fundamental Score: UNKNOWN (no data source)"],
    )
    assert "fundamentals unavailable" in explanation.why_this_stock.lower()
    assert "Trend direction favorable" in explanation.why_this_stock
    assert explanation.risks == ["Fundamental Score: UNKNOWN (no data source)"]


def test_explain_decision_why_now_reflects_setup_state() -> None:
    pre_breakout = explain_decision(
        fundamental_score=70.0,
        technical_reasons=[],
        fundamental_reasons=[],
        setup_state="PRE_BREAKOUT",
        passed_rules=[],
        failed_rules=[],
        risk_flags=[],
    )
    momentum = explain_decision(
        fundamental_score=70.0,
        technical_reasons=[],
        fundamental_reasons=[],
        setup_state="MOMENTUM",
        passed_rules=[],
        failed_rules=[],
        risk_flags=[],
    )
    assert pre_breakout.why_now != momentum.why_now
    assert "approaching" in pre_breakout.why_now.lower()
    assert "extending" in momentum.why_now.lower()


def test_explain_decision_translates_rule_names_to_readable_labels() -> None:
    explanation = explain_decision(
        fundamental_score=70.0,
        technical_reasons=[],
        fundamental_reasons=[],
        setup_state=None,
        passed_rules=["relative_volume>=threshold"],
        failed_rules=["adx"],
        risk_flags=[],
    )
    assert explanation.what_confirms == ["Relative volume clears the scanner's minimum threshold"]
    assert explanation.what_has_not_been_confirmed == ["Trend strength (ADX) is confirmed"]
    # never leaks a raw rule identifier into the user-facing text
    assert "relative_volume>=threshold" not in explanation.what_confirms[0]
