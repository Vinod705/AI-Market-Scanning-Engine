"""Fundamental Score computation.

Every factor is scored independently and tagged with its category, raw
value, normalized 0-100 sub-score, weight, and a plain-English reason —
so the final score is always traceable back to *why*, per the project's
explicit "never an unexplained numeric score" requirement.

A missing raw value is excluded from the weighted average entirely (its
weight is redistributed across whatever factors ARE known), never
treated as a zero. `data_completeness_pct` reports how much of the
factor set was actually available, and
`Settings.fundamental_min_data_completeness_pct` decides when the result
is trustworthy enough to call GOOD/WEAK at all versus UNKNOWN.

The normalization bands below (e.g. ROE >= 20% -> 100) are a documented,
configurable heuristic for scoring quality/value — not a claim about
predictive power, and not a probability. See `app.decision` for the
"SCORE IS NOT PROBABILITY" framing this mirrors.
"""

from collections import Counter
from collections.abc import Callable

from app.config.settings import Settings
from app.fundamentals.models import (
    FactorStatus,
    FundamentalData,
    FundamentalFactor,
    FundamentalScoreResult,
    FundamentalTier,
)

_POSITIVE_THRESHOLD = 60.0
_GOOD_TIER_THRESHOLD = 55.0


def _score_higher_is_better(value: float, bands: list[tuple[float, float]]) -> float:
    for threshold, score in bands:
        if value >= threshold:
            return score
    return bands[-1][1]


def _score_lower_is_better(value: float, bands: list[tuple[float, float]]) -> float:
    for threshold, score in bands:
        if value <= threshold:
            return score
    return bands[-1][1]


def _score_growth(value: float) -> float:
    return _score_higher_is_better(
        value, [(20, 100.0), (12, 80.0), (6, 60.0), (0, 40.0), (-999, 20.0)]
    )


def _score_roe_roce(value: float) -> float:
    return _score_higher_is_better(
        value, [(20, 100.0), (15, 80.0), (10, 60.0), (5, 40.0), (-999, 20.0)]
    )


def _score_margin(value: float) -> float:
    return _score_higher_is_better(
        value, [(25, 100.0), (15, 80.0), (8, 60.0), (3, 40.0), (-999, 20.0)]
    )


def _score_debt_to_equity(value: float) -> float:
    return _score_lower_is_better(
        value, [(0.3, 100.0), (0.5, 80.0), (1.0, 60.0), (2.0, 40.0), (999, 20.0)]
    )


def _score_interest_coverage(value: float) -> float:
    return _score_higher_is_better(
        value, [(10, 100.0), (5, 80.0), (2, 60.0), (1, 40.0), (-999, 20.0)]
    )


def _score_cash_flow_positive(value: float) -> float:
    return 80.0 if value > 0 else 20.0


def _score_pe(value: float) -> float:
    # A non-positive P/E (loss-making) is neither rewarded nor punished by
    # this factor alone — it's scored neutral, not penalized, since it's
    # common and expected for IPOs/growth names.
    if value <= 0:
        return 50.0
    return _score_lower_is_better(value, [(15, 90.0), (25, 70.0), (40, 50.0), (999, 30.0)])


def _score_promoter_holding(value: float) -> float:
    return _score_higher_is_better(value, [(50, 90.0), (30, 70.0), (15, 50.0), (-999, 30.0)])


def _score_promoter_pledge(value: float) -> float:
    return _score_lower_is_better(value, [(0, 100.0), (5, 70.0), (20, 40.0), (999, 10.0)])


# (factor_name, category, extractor, normalizer, human-readable label)
_FactorDef = tuple[
    str, str, Callable[[FundamentalData], float | None], Callable[[float], float], str
]

_FACTOR_DEFS: list[_FactorDef] = [
    (
        "revenue_growth_3y",
        "growth",
        lambda d: d.revenue_growth_3y_pct,
        _score_growth,
        "Revenue growth (3y)",
    ),
    ("eps_growth_3y", "growth", lambda d: d.eps_growth_3y_pct, _score_growth, "EPS growth (3y)"),
    (
        "profit_growth_3y",
        "growth",
        lambda d: d.profit_growth_3y_pct,
        _score_growth,
        "Profit growth (3y)",
    ),
    ("roe", "profitability", lambda d: d.roe_pct, _score_roe_roce, "Return on equity"),
    ("roce", "profitability", lambda d: d.roce_pct, _score_roe_roce, "Return on capital employed"),
    (
        "operating_margin",
        "profitability",
        lambda d: d.operating_margin_pct,
        _score_margin,
        "Operating margin",
    ),
    ("net_margin", "profitability", lambda d: d.net_margin_pct, _score_margin, "Net margin"),
    (
        "debt_to_equity",
        "financial_strength",
        lambda d: d.debt_to_equity,
        _score_debt_to_equity,
        "Debt-to-equity",
    ),
    (
        "interest_coverage",
        "financial_strength",
        lambda d: d.interest_coverage,
        _score_interest_coverage,
        "Interest coverage",
    ),
    (
        "operating_cash_flow",
        "cash_flow",
        lambda d: d.operating_cash_flow_cr,
        _score_cash_flow_positive,
        "Operating cash flow",
    ),
    (
        "free_cash_flow",
        "cash_flow",
        lambda d: d.free_cash_flow_cr,
        _score_cash_flow_positive,
        "Free cash flow",
    ),
    ("pe", "valuation", lambda d: d.pe, _score_pe, "Valuation (P/E)"),
    (
        "promoter_holding",
        "ownership",
        lambda d: d.promoter_holding_pct,
        _score_promoter_holding,
        "Promoter holding",
    ),
    (
        "promoter_pledge",
        "ownership",
        lambda d: d.promoter_pledge_pct,
        _score_promoter_pledge,
        "Promoter pledge",
    ),
]

_CATEGORY_WEIGHT_ATTR = {
    "growth": "fundamental_weight_growth",
    "profitability": "fundamental_weight_profitability",
    "financial_strength": "fundamental_weight_financial_strength",
    "cash_flow": "fundamental_weight_cash_flow",
    "valuation": "fundamental_weight_valuation",
    "ownership": "fundamental_weight_ownership",
}


class FundamentalScorer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def score(self, data: FundamentalData | None) -> FundamentalScoreResult:
        if data is None:
            return FundamentalScoreResult(
                tier=FundamentalTier.UNKNOWN,
                score=None,
                data_completeness_pct=0.0,
                factors=[],
                positive_reasons=[],
                negative_reasons=[],
                unknown_reasons=[
                    "Fundamental data unavailable — no data source currently integrated"
                ],
                risk_flags=["Fundamental Score: UNKNOWN (no data source)"],
            )

        category_counts = Counter(category for _, category, *_rest in _FACTOR_DEFS)
        factors: list[FundamentalFactor] = []

        for name, category, extractor, normalizer, label in _FACTOR_DEFS:
            category_weight = getattr(self._settings, _CATEGORY_WEIGHT_ATTR[category])
            per_factor_weight = category_weight / category_counts[category]
            raw = extractor(data)

            if raw is None:
                factors.append(
                    FundamentalFactor(
                        factor_name=name,
                        category=category,
                        value=None,
                        normalized_score=None,
                        weight=per_factor_weight,
                        contribution=0.0,
                        status=FactorStatus.UNKNOWN,
                        reason=f"{label} unavailable",
                    )
                )
                continue

            normalized = normalizer(raw)
            status = (
                FactorStatus.POSITIVE
                if normalized >= _POSITIVE_THRESHOLD
                else FactorStatus.NEGATIVE
            )
            factors.append(
                FundamentalFactor(
                    factor_name=name,
                    category=category,
                    value=f"{raw:g}",
                    normalized_score=normalized,
                    weight=per_factor_weight,
                    contribution=normalized * per_factor_weight,
                    status=status,
                    reason=(
                        f"{label} {'strong' if status == FactorStatus.POSITIVE else 'weak'} at {raw:g}"
                    ),
                )
            )

        known_factors = [f for f in factors if f.normalized_score is not None]
        data_completeness_pct = 100.0 * len(known_factors) / len(factors)

        weight_sum = sum(f.weight for f in known_factors)
        overall_score = (
            sum(f.contribution for f in known_factors) / weight_sum if weight_sum > 0 else None
        )

        positive_reasons = [f.reason for f in factors if f.status == FactorStatus.POSITIVE]
        negative_reasons = [f.reason for f in factors if f.status == FactorStatus.NEGATIVE]
        unknown_reasons = [f.reason for f in factors if f.status == FactorStatus.UNKNOWN]

        risk_flags = list(data.risk_notes)
        if data.promoter_pledge_pct is not None and data.promoter_pledge_pct > 20:
            risk_flags.append(f"High promoter pledge ({data.promoter_pledge_pct:g}%)")
        if data.debt_to_equity is not None and data.debt_to_equity > 2.0:
            risk_flags.append(f"High debt-to-equity ({data.debt_to_equity:g})")
        if data_completeness_pct < self._settings.fundamental_min_data_completeness_pct:
            risk_flags.append(f"Limited fundamental data ({data_completeness_pct:.0f}% complete)")

        if overall_score is None or (
            data_completeness_pct < self._settings.fundamental_min_data_completeness_pct
        ):
            tier = FundamentalTier.UNKNOWN
            reported_score = None
        elif overall_score >= _GOOD_TIER_THRESHOLD:
            tier = FundamentalTier.GOOD
            reported_score = overall_score
        else:
            tier = FundamentalTier.WEAK
            reported_score = overall_score

        return FundamentalScoreResult(
            tier=tier,
            score=reported_score,
            data_completeness_pct=data_completeness_pct,
            factors=factors,
            positive_reasons=positive_reasons,
            negative_reasons=negative_reasons,
            unknown_reasons=unknown_reasons,
            risk_flags=risk_flags,
        )
