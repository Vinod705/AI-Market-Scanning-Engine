"""Support/resistance features: rolling S/R levels, floor pivot, breakout level, Fibonacci pullback zone."""

import pandas as pd

from app.features import indicators

_SR_WINDOW = 20
_FIB_UPPER_RATIO = 0.382
_FIB_LOWER_RATIO = 0.5


class SupportResistanceFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]

        # Simple rolling N-bar high/low — distinct from the fractal swing
        # points in app/features/structure/ (those mark turning points;
        # these are the "level a breakout needs to clear" style S/R).
        support_level = low.rolling(window=_SR_WINDOW, min_periods=_SR_WINDOW).min()
        resistance_level = high.rolling(window=_SR_WINDOW, min_periods=_SR_WINDOW).max()
        breakout_level = resistance_level

        # Same formula as indicators.floor_pivot, computed elementwise here
        # since that helper is scalar-typed for ad-hoc single-value use.
        prev_high, prev_low, prev_close = high.shift(1), low.shift(1), close.shift(1)
        pivot_point = (prev_high + prev_low + prev_close) / 3.0

        is_swing_high, is_swing_low = indicators.swing_points(high, low, window=2)
        swing_high = high.where(is_swing_high).ffill()
        swing_low = low.where(is_swing_low).ffill()

        # Fibonacci retracement zone of the most recent swing move, measured
        # down from the swing high.
        move = swing_high - swing_low
        pullback_zone_high = swing_high - _FIB_UPPER_RATIO * move
        pullback_zone_low = swing_high - _FIB_LOWER_RATIO * move

        return pd.DataFrame(
            {
                "support_level": support_level,
                "resistance_level": resistance_level,
                "pivot_point": pivot_point,
                "breakout_level": breakout_level,
                "pullback_zone_low": pullback_zone_low,
                "pullback_zone_high": pullback_zone_high,
            },
            index=df.index,
        )
