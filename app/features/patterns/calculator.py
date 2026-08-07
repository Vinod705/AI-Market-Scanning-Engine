"""Chart pattern features.

Chart patterns don't have single, universally-agreed formulas the way RSI or
ATR do. These are documented, rule-based heuristics meant as scanner
pre-filters, not authoritative pattern recognition — treat a `True` flag as
"worth a human/AI look", not as a certified pattern match.

`pattern_ipo_base` is a partial exception: a correct implementation needs
`Symbol.listing_date`, which this OHLCV-only calculator doesn't receive (the
shared `calculate(df)` contract every category follows). It approximates
"early in the stock's life" using position within the loaded price window
instead — accurate only when that window starts at the actual listing date.
"""

import numpy as np
import pandas as pd


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    def slope(values: np.ndarray) -> float:
        x = np.arange(len(values))
        return float(np.polyfit(x, values, 1)[0])

    return series.rolling(window=window, min_periods=window).apply(slope, raw=True)


def _rolling_range_pct(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    return (high.rolling(window).max() - low.rolling(window).min()) / close.replace(0, np.nan) * 100


class PatternFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]

        # --- Triangle: converging highs (falling) and lows (rising) ---
        tri_window = 15
        high_slope = _rolling_slope(high, tri_window)
        low_slope = _rolling_slope(low, tri_window)
        pattern_triangle = (high_slope < 0) & (low_slope > 0)

        # --- Bull/bear flag: sharp impulse move, then a tight opposite-sloping consolidation ---
        impulse_window, flag_window = 10, 5
        impulse_return = close.pct_change(periods=impulse_window).shift(flag_window)
        flag_range_pct = _rolling_range_pct(high, low, close, flag_window)
        flag_slope = _rolling_slope(close, flag_window)
        pattern_bull_flag = (impulse_return > 0.15) & (flag_range_pct < 10) & (flag_slope <= 0)
        pattern_bear_flag = (impulse_return < -0.15) & (flag_range_pct < 10) & (flag_slope >= 0)

        # --- Flat base / rectangle: tight horizontal consolidation ---
        base_window = 15
        base_range_pct = _rolling_range_pct(high, low, close, base_window)
        base_high = high.rolling(base_window).max()
        near_base_highs = close >= base_high * 0.90
        pattern_flat_base = (base_range_pct < 15) & near_base_highs
        pattern_rectangle = (base_range_pct < 12) & ~near_base_highs

        # --- IPO base: tight consolidation early in the loaded window (see module docstring) ---
        bars_from_window_start = pd.Series(np.arange(len(df)), index=df.index)
        pattern_ipo_base = (bars_from_window_start < 40) & pattern_flat_base

        # --- Cup & handle: U-shaped recovery back near the prior high, then a small pullback ---
        cup_window, handle_window = 40, 8
        cup_start_high = high.shift(cup_window).rolling(cup_window).max()
        low_position_in_cup = low.rolling(cup_window).apply(
            lambda v: float(np.argmin(v)) / len(v), raw=True
        )
        u_shaped = low_position_in_cup.between(0.25, 0.75)
        recovered_near_high = close >= cup_start_high * 0.90
        handle_range_pct = _rolling_range_pct(high, low, close, handle_window)
        pattern_cup_handle = u_shaped & recovered_near_high & (handle_range_pct < 8)

        # --- Volatility Contraction Pattern: each successive 10-bar range tighter than the last ---
        vcp_leg = 10
        range_leg_1 = _rolling_range_pct(high, low, close, vcp_leg)
        range_leg_2 = range_leg_1.shift(vcp_leg)
        range_leg_3 = range_leg_1.shift(2 * vcp_leg)
        pattern_vcp = (range_leg_1 < range_leg_2) & (range_leg_2 < range_leg_3)

        return pd.DataFrame(
            {
                "pattern_triangle": pattern_triangle.fillna(False),
                "pattern_bull_flag": pattern_bull_flag.fillna(False),
                "pattern_bear_flag": pattern_bear_flag.fillna(False),
                "pattern_flat_base": pattern_flat_base.fillna(False),
                "pattern_ipo_base": pattern_ipo_base.fillna(False),
                "pattern_rectangle": pattern_rectangle.fillna(False),
                "pattern_cup_handle": pattern_cup_handle.fillna(False),
                "pattern_vcp": pattern_vcp.fillna(False),
            },
            index=df.index,
        )
