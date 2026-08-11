"""Stop-loss and risk/reward derivation.

Reuses `support_level` (Phase 3 support/resistance features) and the
already-computed ATR(14) volatility feature — never a new data source.
When both are available, the tighter (nearer-to-price) of the two is
used: a valid stop should reflect realistic near-term invalidation, not
the widest possible level. `swing_low` is only a last-resort fallback
when neither support nor ATR is available.

Returns `stop_loss=None` when no level below the current price is
available at all, rather than inventing one.
"""

from app.compounding.models import RiskResult

_DEFAULT_ATR_MULTIPLIER = 1.5


def derive_risk(
    *,
    price: float | None,
    target_price: float | None,
    support_level: float | None,
    swing_low: float | None,
    atr14: float | None,
    atr_multiplier: float = _DEFAULT_ATR_MULTIPLIER,
) -> RiskResult:
    if price is None or price <= 0:
        return RiskResult(None, None, None, "current price unavailable")

    candidates: list[tuple[float, str]] = []
    if support_level is not None and 0 < support_level < price:
        candidates.append((support_level, "support level"))
    if atr14 is not None and atr14 > 0:
        candidates.append((price - atr14 * atr_multiplier, f"ATR(14) x {atr_multiplier:g}"))
    if not candidates and swing_low is not None and 0 < swing_low < price:
        candidates.append((swing_low, "swing low"))

    if not candidates:
        return RiskResult(
            None, None, None, "no support/ATR/swing-low level available below current price"
        )

    # Tighter (higher, since stops sit below price) of whichever candidates
    # are available.
    stop_loss, basis = max(candidates, key=lambda c: c[0])
    if stop_loss <= 0:
        return RiskResult(None, None, None, "derived stop-loss is non-positive")

    risk_pct = round(((price - stop_loss) / price) * 100, 2)

    reward_risk_ratio: float | None = None
    if target_price is not None:
        risk_amount = price - stop_loss
        if risk_amount > 0:
            reward_risk_ratio = round((target_price - price) / risk_amount, 2)

    return RiskResult(round(stop_loss, 2), risk_pct, reward_risk_ratio, basis)
