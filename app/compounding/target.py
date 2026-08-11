"""Potential-upside target derivation.

Reuses only technical levels the Phase 3 feature engine already computes
(`support_level`/`resistance_level` from `app.features.support_resistance`,
`swing_high` from `app.features.structure`) — never a new data source,
never an invented number.

Primary method is the standard "measured move" range-projection technique:
project the height of the recent support-resistance range above the
breakout point (`resistance + (resistance - support)`). This is a
textbook technical-analysis technique, not a fabricated heuristic, and it
naturally gives an honest (sometimes small or negative) upside for a
stock whose original breakout objective has already played out — that is
correct behavior for a compounding strategy, not a bug: it should not
chase a move that's already run.

Returns `target_price=None` / `TargetClassification.UNKNOWN` when none of
the required levels are available (e.g. a recent IPO with fewer than the
20 trading days `SupportResistanceFeatureCalculator` needs) rather than
guessing.
"""

from app.compounding.models import TargetClassification, TargetResult

# <5%=NOT_ATTRACTIVE, 5-8%=MINIMUM_OPPORTUNITY, 8-12%=GOOD, 12-15%=STRONG,
# 15-20%=VERY_STRONG, >20%=EXCEPTIONAL — user-specified bands, checked
# highest-first.
_BANDS: list[tuple[float, TargetClassification]] = [
    (20.0, TargetClassification.EXCEPTIONAL),
    (15.0, TargetClassification.VERY_STRONG),
    (12.0, TargetClassification.STRONG),
    (8.0, TargetClassification.GOOD),
    (5.0, TargetClassification.MINIMUM_OPPORTUNITY),
]


def classify_upside(potential_upside_pct: float | None) -> TargetClassification:
    if potential_upside_pct is None:
        return TargetClassification.UNKNOWN
    for threshold, label in _BANDS:
        if potential_upside_pct >= threshold:
            return label
    return TargetClassification.NOT_ATTRACTIVE


def derive_target(
    *,
    price: float | None,
    resistance_level: float | None,
    support_level: float | None,
    swing_high: float | None,
) -> TargetResult:
    if price is None or price <= 0:
        return TargetResult(None, None, TargetClassification.UNKNOWN, "current price unavailable")

    target_price: float | None = None
    basis = ""

    if (
        resistance_level is not None
        and support_level is not None
        and resistance_level > support_level
    ):
        target_price = resistance_level + (resistance_level - support_level)
        basis = "measured move: resistance + (resistance - support)"
    elif resistance_level is not None and resistance_level > price:
        target_price = resistance_level
        basis = "resistance level (not yet cleared)"
    elif swing_high is not None and swing_high > price:
        target_price = swing_high
        basis = "most recent swing high"
    else:
        return TargetResult(
            None,
            None,
            TargetClassification.UNKNOWN,
            "no resistance/support/swing-high level available to project a target from",
        )

    potential_upside_pct = round(((target_price / price) - 1) * 100, 2)
    classification = classify_upside(potential_upside_pct)
    return TargetResult(round(target_price, 2), potential_upside_pct, classification, basis)
