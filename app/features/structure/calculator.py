"""Market structure features: swing points, regression trend channel, range/consolidation, base length."""

import numpy as np
import pandas as pd

from app.features import indicators

# Thresholds are documented, tunable heuristics — there's no single agreed
# definition of "range" vs "consolidation" in technical analysis.
_RANGE_WINDOW = 20
_RANGE_THRESHOLD_PCT = 15.0
_CONSOLIDATION_WINDOW = 10
_CONSOLIDATION_THRESHOLD_PCT = 8.0
_CHANNEL_WINDOW = 20


def _regression_line_value(values: np.ndarray) -> float:
    """Value of the best-fit line through `values` at its most recent point."""
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    return float(slope * (len(values) - 1) + intercept)


class StructureFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]

        is_swing_high, is_swing_low = indicators.swing_points(high, low, window=2)
        swing_high = high.where(is_swing_high).ffill()
        swing_low = low.where(is_swing_low).ffill()

        regression_line = close.rolling(window=_CHANNEL_WINDOW, min_periods=_CHANNEL_WINDOW).apply(
            _regression_line_value, raw=True
        )
        channel_std = close.rolling(window=_CHANNEL_WINDOW, min_periods=_CHANNEL_WINDOW).std()
        trend_channel_upper = regression_line + 2 * channel_std
        trend_channel_lower = regression_line - 2 * channel_std

        range_width_pct = (
            (high.rolling(_RANGE_WINDOW).max() - low.rolling(_RANGE_WINDOW).min())
            / close.replace(0, np.nan)
            * 100
        )
        is_range = range_width_pct < _RANGE_THRESHOLD_PCT

        consolidation_width_pct = (
            (high.rolling(_CONSOLIDATION_WINDOW).max() - low.rolling(_CONSOLIDATION_WINDOW).min())
            / close.replace(0, np.nan)
            * 100
        )
        is_consolidation = (consolidation_width_pct < _CONSOLIDATION_THRESHOLD_PCT).fillna(False)

        # Consecutive-day streak of `is_consolidation`, reset to 0 outside it.
        streak_id = (~is_consolidation).cumsum()
        base_length_days = is_consolidation.groupby(streak_id).cumcount() + 1
        base_length_days = base_length_days.where(is_consolidation, 0)

        return pd.DataFrame(
            {
                "swing_high": swing_high,
                "swing_low": swing_low,
                "trend_channel_upper": trend_channel_upper,
                "trend_channel_lower": trend_channel_lower,
                "is_range": is_range.fillna(False),
                "is_consolidation": is_consolidation,
                "base_length_days": base_length_days,
                "range_width_pct": range_width_pct,
            },
            index=df.index,
        )
