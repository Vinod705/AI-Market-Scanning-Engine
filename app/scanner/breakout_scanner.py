"""Breakout Scanner v1: EMA stack + ADX + volume + resistance-proximity setup.

Qualifies a symbol when *all* of the following hold, each threshold coming
from `Settings` rather than being hardcoded:

- price > EMA20 > EMA50 > EMA200 (an established, aligned uptrend)
- ADX(14) > `scanner_breakout_adx_threshold` (trend has real strength, not chop)
- relative volume > `scanner_breakout_relative_volume_threshold` (unusual interest)
- relative volume >= `scanner_breakout_volume_increasing_min_relative_volume`
  (a separate, looser check: volume isn't declining — see settings.py)
- price is within `scanner_breakout_resistance_proximity_pct` of `resistance_level`
  (actionable now, not "watch and wait")
"""

from app.config.settings import Settings
from app.scanner.base_scanner import BaseScanner
from app.scanner.models import ScanContext, ScannerContext, ScanOutcome, ValidationResult
from app.scanner.validator import ScannerValidator

_REQUIRED_FIELDS = ["ema20", "ema50", "ema200", "adx14", "relative_volume", "resistance_level"]
_RANGE_CHECKS: dict[str, tuple[float, float]] = {
    "adx14": (0.0, 100.0),
    "relative_volume": (0.0, 1000.0),
}


class BreakoutScanner(BaseScanner):
    name = "breakout_v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate(self, context: ScannerContext) -> ValidationResult:
        assert isinstance(context, ScanContext)
        field_check = ScannerValidator.require_fields(context, ["price", *_REQUIRED_FIELDS])
        if not field_check.valid:
            return field_check
        return ScannerValidator.require_ranges(context, _RANGE_CHECKS)

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, ScanContext)
        features = context.features
        settings = self._settings

        # validate() having passed guarantees all of these are non-None;
        # assert narrows the ORM's `Decimal | None` columns for mypy.
        price = context.price
        ema20, ema50, ema200 = features.ema20, features.ema50, features.ema200
        adx14, relative_volume = features.adx14, features.relative_volume
        assert price is not None
        assert ema20 is not None and ema50 is not None and ema200 is not None
        assert adx14 is not None and relative_volume is not None

        checks = {
            "price>EMA20": price > ema20,
            "EMA20>EMA50": ema20 > ema50,
            "EMA50>EMA200": ema50 > ema200,
            "ADX>threshold": float(adx14) > settings.scanner_breakout_adx_threshold,
            "relative_volume>threshold": (
                float(relative_volume) > settings.scanner_breakout_relative_volume_threshold
            ),
            "volume_increasing": (
                float(relative_volume)
                >= settings.scanner_breakout_volume_increasing_min_relative_volume
            ),
            "near_resistance": self._within_resistance_proximity(context),
        }

        qualified = all(checks.values())
        if qualified:
            reason = "all conditions met: " + ", ".join(checks)
        else:
            failed = [label for label, passed in checks.items() if not passed]
            reason = f"failed {len(failed)}/{len(checks)}: " + ", ".join(failed)

        return ScanOutcome(qualified=qualified, reason=reason)

    def score(self, context: ScannerContext) -> float:
        assert isinstance(context, ScanContext)
        features = context.features
        settings = self._settings

        trend = float(features.trend_strength or 0)
        momentum = (float(features.momentum_score or 0) + 100) / 2
        volume = min(float(features.relative_volume or 0) / 2.0, 1.0) * 100
        volatility = (
            100.0 if features.atr_expansion else (30.0 if features.atr_contraction else 60.0)
        )
        relative_strength = (
            50.0
            if features.rs_vs_nifty is None
            else max(0.0, min(100.0, 50.0 + float(features.rs_vs_nifty)))
        )
        support_resistance = self._resistance_proximity_score(context)

        total = (
            trend * settings.scanner_score_weight_trend
            + momentum * settings.scanner_score_weight_momentum
            + volume * settings.scanner_score_weight_volume
            + volatility * settings.scanner_score_weight_volatility
            + relative_strength * settings.scanner_score_weight_relative_strength
            + support_resistance * settings.scanner_score_weight_support_resistance
        )
        return round(min(max(total, 0.0), 100.0), 2)

    # --- internals -----------------------------------------------------

    def _resistance_distance_pct(self, context: ScanContext) -> float | None:
        resistance = context.features.resistance_level
        if resistance is None or context.price is None or resistance == 0:
            return None
        return abs(float(context.price) - float(resistance)) / float(resistance) * 100.0

    def _within_resistance_proximity(self, context: ScanContext) -> bool:
        distance_pct = self._resistance_distance_pct(context)
        if distance_pct is None:
            return False
        return distance_pct <= self._settings.scanner_breakout_resistance_proximity_pct

    def _resistance_proximity_score(self, context: ScanContext) -> float:
        distance_pct = self._resistance_distance_pct(context)
        if distance_pct is None:
            return 0.0
        threshold = self._settings.scanner_breakout_resistance_proximity_pct
        if threshold <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 - (distance_pct / threshold) * 100.0))
