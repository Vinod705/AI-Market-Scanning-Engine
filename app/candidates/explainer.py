"""Deterministic explainability for IPO/F&O candidates — Phase 6.

Every function here is a pure transformation of data the system already
computed (`StockCandidate`'s persisted factor breakdown, the Decision
Engine's rule results) into a human-readable shape. Nothing here calls an
LLM or invents a reason that isn't traceable to an actual factor, rule,
threshold, or weight — that's the whole point ("no black box", per the
spec): a user must be able to see why a stock was selected without
reading the source code, but everything they see must still BE the
source code's actual output, just formatted.

Reused for both IPO and F&O categories — there is exactly one scoring
system (`app.fundamentals.scorer`, `app.technical.scorer`) and exactly
one explanation engine. Nothing here branches on universe.
"""

from dataclasses import dataclass

from app.config.settings import Settings

# --- Category rollups (Technical/Fundamental score breakdowns) ---

_TECHNICAL_CATEGORY_LABELS = {
    "trend": "Trend",
    "momentum": "Momentum",
    "volume": "Volume",
    "volatility": "Volatility",
    "vwap": "VWAP",
    "structure": "Structure",
}
_FUNDAMENTAL_CATEGORY_LABELS = {
    "growth": "Growth",
    "profitability": "Profitability",
    "financial_strength": "Financial Strength",
    "cash_flow": "Cash Flow",
    "valuation": "Valuation",
    "ownership": "Ownership",
}


def technical_category_weights(settings: Settings) -> dict[str, float]:
    return {
        "trend": settings.technical_weight_trend,
        "momentum": settings.technical_weight_momentum,
        "volume": settings.technical_weight_volume,
        "volatility": settings.technical_weight_volatility,
        "vwap": settings.technical_weight_vwap,
        "structure": settings.technical_weight_structure,
    }


def fundamental_category_weights(settings: Settings) -> dict[str, float]:
    return {
        "growth": settings.fundamental_weight_growth,
        "profitability": settings.fundamental_weight_profitability,
        "financial_strength": settings.fundamental_weight_financial_strength,
        "cash_flow": settings.fundamental_weight_cash_flow,
        "valuation": settings.fundamental_weight_valuation,
        "ownership": settings.fundamental_weight_ownership,
    }


@dataclass
class CategoryBreakdown:
    category: str
    label: str
    score: float  # summed contribution from this category's known factors
    max_score: (
        float  # category_weight * 100 — the ceiling if every factor in it were maximally favorable
    )
    factors: list[dict[str, object]]


def _factor_contribution(factor: dict[str, object]) -> float:
    raw = factor.get("contribution")
    if isinstance(raw, int | float):
        return float(raw)
    return 0.0


def rollup_by_category(
    factors: list[dict[str, object]], weights: dict[str, float], labels: dict[str, str]
) -> list[CategoryBreakdown]:
    """Groups a flat factor list (as persisted by `StockCandidate.to_feature_snapshot()`)
    into per-category score/max_score pairs, e.g. `Trend: 18/25`. `max_score`
    is the category's configured weight scaled to 100 — the ceiling every
    factor in that category could contribute if fully favorable, not an
    arbitrary display number."""
    result: list[CategoryBreakdown] = []
    for category, weight in weights.items():
        category_factors = [f for f in factors if f.get("category") == category]
        score = sum(_factor_contribution(f) for f in category_factors)
        result.append(
            CategoryBreakdown(
                category=category,
                label=labels.get(category, category.replace("_", " ").title()),
                score=round(score, 2),
                max_score=round(weight * 100, 2),
                factors=category_factors,
            )
        )
    return result


def technical_breakdown(
    factors: list[dict[str, object]], settings: Settings
) -> list[CategoryBreakdown]:
    return rollup_by_category(
        factors, technical_category_weights(settings), _TECHNICAL_CATEGORY_LABELS
    )


def fundamental_breakdown(
    factors: list[dict[str, object]], settings: Settings
) -> list[CategoryBreakdown]:
    return rollup_by_category(
        factors, fundamental_category_weights(settings), _FUNDAMENTAL_CATEGORY_LABELS
    )


# --- Decision explanation (WHY THIS STOCK / WHY NOW / CONFIRMS / RISKS) ---

_SETUP_STATE_WHY_NOW = {
    "PRE_BREAKOUT": "Price is approaching resistance but has not broken through yet.",
    "BREAKOUT_CONFIRMED": "Price has broken through resistance with volume confirmation.",
    "MOMENTUM": "Price is extending beyond resistance with sustained trend strength.",
}

# Human labels for every rule name that can appear in passed_rules/failed_rules —
# both the candidate scanners' own checks (app.candidates.*_scanner) and the
# Decision Engine's re-validation rules (app.decision.rules) — so "WHAT
# CONFIRMS"/"WHAT HAS NOT BEEN CONFIRMED" reads as English, not rule ids.
_RULE_LABELS = {
    # scanner-level checks
    "setup_state_is_momentum_or_confirmed": "Setup has reached a confirmed breakout or momentum stage",
    "setup_state_is_pre_breakout": "Setup is in a pre-breakout stage",
    "relative_volume>=threshold": "Relative volume clears the scanner's minimum threshold",
    "adx>=threshold": "ADX (trend strength) clears the scanner's minimum threshold",
    "overall_score>=threshold": "Overall Setup Score clears the scanner's minimum threshold",
    # decision-engine rules
    "required_features": "All required data fields are present",
    "data_freshness": "Scanner result is from a recent trading day",
    "minimum_score": "Score meets the Decision Engine's minimum alert threshold",
    "trend": "Trend is aligned (EMA stack)",
    "relative_volume": "Relative volume confirms increased participation",
    "adx": "Trend strength (ADX) is confirmed",
    "resistance_proximity": "Price is close to the breakout/resistance level",
    "market_session": "Market is currently open",
}


def _label_rule(rule_name: str) -> str:
    return _RULE_LABELS.get(rule_name, rule_name.replace("_", " ").replace(">=", " >= "))


@dataclass
class DecisionExplanation:
    why_this_stock: str
    why_now: str
    what_confirms: list[str]
    what_has_not_been_confirmed: list[str]
    risks: list[str]


def explain_decision(
    *,
    fundamental_score: float | None,
    technical_reasons: list[str],
    fundamental_reasons: list[str],
    setup_state: str | None,
    passed_rules: list[str],
    failed_rules: list[str],
    risk_flags: list[str],
) -> DecisionExplanation:
    """Builds the plain-English decision narrative purely from the
    candidate's own recorded reasons/rules — no field here is generated
    independently of the underlying data."""
    technical_bit = (
        f"strong technical setup ({', '.join(technical_reasons[:3])})"
        if technical_reasons
        else "a technical setup meeting the scanner's criteria"
    )
    if fundamental_score is None:
        fundamental_bit = "fundamentals unavailable (no data source integrated)"
    elif fundamental_reasons:
        fundamental_bit = f"supportive fundamentals ({', '.join(fundamental_reasons[:3])})"
    else:
        fundamental_bit = "fundamentals that do not add further support"
    # str.capitalize() would also lowercase every other character in the
    # string (including inside the parenthesized reasons) — only the
    # first letter should change case.
    why_this_stock = f"{technical_bit[0].upper()}{technical_bit[1:]}; {fundamental_bit}."

    why_now = _SETUP_STATE_WHY_NOW.get(
        setup_state or "", "Current price action matches this scanner's entry criteria."
    )

    what_confirms = [_label_rule(r) for r in passed_rules]
    what_has_not_been_confirmed = [_label_rule(r) for r in failed_rules]

    return DecisionExplanation(
        why_this_stock=why_this_stock,
        why_now=why_now,
        what_confirms=what_confirms,
        what_has_not_been_confirmed=what_has_not_been_confirmed,
        risks=list(risk_flags),
    )
