"""Technical Score computation — same factor-level explainability approach
as `app.fundamentals.scorer`: every factor carries its raw value,
normalized 0-100 sub-score, category weight, contribution, and a
plain-English reason.

Like the fundamentals scorer, a factor whose raw input is unavailable
(e.g. `ema200`/`ema50` before enough trading history exists, or
`session_vwap` before the market has opened today) is excluded from the
weighted average rather than counted as zero, and `data_completeness_pct`
reports how much of the factor set was actually available.

These normalization bands (e.g. RSI 55-70 -> 100) are a documented,
configurable scoring heuristic, not a probability — see
`app.decision`'s "SCORE IS NOT PROBABILITY" framing, which applies
equally here.
"""

from collections import Counter
from collections.abc import Callable

from app.config.settings import Settings
from app.technical.inputs import TechnicalInputs
from app.technical.models import FactorStatus, TechnicalFactor, TechnicalScoreResult

_POSITIVE_THRESHOLD = 60.0


def _bands_higher_is_better(value: float, bands: list[tuple[float, float]]) -> float:
    for threshold, score in bands:
        if value >= threshold:
            return score
    return bands[-1][1]


def _score_trend_direction(value: float) -> float:
    # value is pre-mapped: up=1.0, sideways=0.5, down=0.0
    return {1.0: 90.0, 0.5: 50.0, 0.0: 15.0}.get(value, 50.0)


def _score_price_vs_ema(pct_above: float) -> float:
    return _bands_higher_is_better(pct_above, [(3, 100.0), (0, 70.0), (-3, 40.0), (-999, 20.0)])


def _score_rsi(value: float) -> float:
    if 55 <= value <= 70:
        return 100.0
    if 45 <= value < 55 or 70 < value <= 80:
        return 70.0
    if 35 <= value < 45:
        return 40.0
    if value > 80:
        return 40.0
    return 20.0


def _score_macd_histogram(value: float) -> float:
    return 80.0 if value > 0 else 20.0


def _score_adx(value: float) -> float:
    return _bands_higher_is_better(
        value, [(30, 100.0), (25, 80.0), (20, 60.0), (15, 40.0), (0, 20.0)]
    )


def _score_relative_volume(value: float) -> float:
    return _bands_higher_is_better(
        value, [(3, 100.0), (2, 85.0), (1.5, 70.0), (1, 50.0), (0, 30.0)]
    )


def _score_accum_dist(value: float) -> float:
    return _bands_higher_is_better(
        value, [(20, 90.0), (5, 70.0), (-5, 50.0), (-20, 30.0), (-999, 10.0)]
    )


def _score_volatility_state(value: float) -> float:
    # value: 1.0 = squeeze (coiled, building pressure), 0.5 = expanding, 0.0 = neither
    return {1.0: 80.0, 0.5: 65.0}.get(value, 50.0)


def _score_vwap(pct_above: float) -> float:
    return _bands_higher_is_better(pct_above, [(1, 90.0), (0, 65.0), (-1, 40.0), (-999, 20.0)])


def _score_structure(value: float) -> float:
    return {2.0: 90.0, 1.0: 60.0, 0.0: 30.0}.get(value, 50.0)


_FactorDef = tuple[
    str, str, Callable[[TechnicalInputs], float | None], Callable[[float], float], str
]

_FACTOR_DEFS: list[_FactorDef] = [
    (
        "trend_direction",
        "trend",
        lambda i: {"up": 1.0, "sideways": 0.5, "down": 0.0}.get(i.trend_direction or "", None),
        _score_trend_direction,
        "Trend direction",
    ),
    (
        "price_vs_ema20",
        "trend",
        lambda i: ((i.price / i.ema20 - 1) * 100) if i.ema20 else None,
        _score_price_vs_ema,
        "Price vs EMA20",
    ),
    (
        "price_vs_ema50",
        "trend",
        lambda i: ((i.price / i.ema50 - 1) * 100) if i.ema50 else None,
        _score_price_vs_ema,
        "Price vs EMA50",
    ),
    ("rsi14", "momentum", lambda i: i.rsi14, _score_rsi, "RSI(14)"),
    (
        "macd_histogram",
        "momentum",
        lambda i: i.macd_histogram,
        _score_macd_histogram,
        "MACD histogram",
    ),
    ("adx14", "momentum", lambda i: i.adx14, _score_adx, "ADX(14) trend strength"),
    (
        "relative_volume",
        "volume",
        lambda i: i.relative_volume,
        _score_relative_volume,
        "Relative volume",
    ),
    (
        "accumulation_vs_distribution",
        "volume",
        lambda i: (
            (i.accumulation_score - i.distribution_score)
            if i.accumulation_score is not None and i.distribution_score is not None
            else None
        ),
        _score_accum_dist,
        "Accumulation vs distribution",
    ),
    (
        "volatility_state",
        "volatility",
        lambda i: 1.0 if i.volatility_squeeze else (0.5 if i.atr_expansion else 0.0),
        _score_volatility_state,
        "Volatility state",
    ),
    (
        "price_vs_vwap",
        "vwap",
        lambda i: ((i.price / i.session_vwap - 1) * 100) if i.session_vwap else None,
        _score_vwap,
        "Price vs session VWAP",
    ),
    (
        "market_structure",
        "structure",
        lambda i: float(i.higher_high) + float(i.higher_low),
        _score_structure,
        "Market structure (higher highs/lows)",
    ),
]

_CATEGORY_WEIGHT_ATTR = {
    "trend": "technical_weight_trend",
    "momentum": "technical_weight_momentum",
    "volume": "technical_weight_volume",
    "volatility": "technical_weight_volatility",
    "vwap": "technical_weight_vwap",
    "structure": "technical_weight_structure",
}


class TechnicalScorer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def score(self, inputs: TechnicalInputs) -> TechnicalScoreResult:
        category_counts = Counter(category for _, category, *_rest in _FACTOR_DEFS)
        factors: list[TechnicalFactor] = []

        for name, category, extractor, normalizer, label in _FACTOR_DEFS:
            category_weight = getattr(self._settings, _CATEGORY_WEIGHT_ATTR[category])
            per_factor_weight = category_weight / category_counts[category]
            raw = extractor(inputs)

            if raw is None:
                factors.append(
                    TechnicalFactor(
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
                TechnicalFactor(
                    factor_name=name,
                    category=category,
                    value=f"{raw:g}",
                    normalized_score=normalized,
                    weight=per_factor_weight,
                    contribution=normalized * per_factor_weight,
                    status=status,
                    reason=(
                        f"{label} {'favorable' if status == FactorStatus.POSITIVE else 'unfavorable'}"
                    ),
                )
            )

        known_factors = [f for f in factors if f.normalized_score is not None]
        data_completeness_pct = 100.0 * len(known_factors) / len(factors)
        weight_sum = sum(f.weight for f in known_factors)
        # Technical inputs are always at least partially available once
        # Phase 3 has run for a symbol (price/volume are never missing);
        # a 0-weight fallback would only happen for a symbol with no
        # feature row at all, which callers should not be scoring anyway.
        score = sum(f.contribution for f in known_factors) / weight_sum if weight_sum > 0 else 0.0

        return TechnicalScoreResult(
            score=score,
            data_completeness_pct=data_completeness_pct,
            factors=factors,
            positive_reasons=[f.reason for f in factors if f.status == FactorStatus.POSITIVE],
            negative_reasons=[f.reason for f in factors if f.status == FactorStatus.NEGATIVE],
            unknown_reasons=[f.reason for f in factors if f.status == FactorStatus.UNKNOWN],
        )
