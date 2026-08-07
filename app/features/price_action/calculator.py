"""Price action features: HH/HL/LH/LL, structure breaks, inside/outside bars, gaps, NR4/NR7."""

import pandas as pd


class PriceActionFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]
        prev_high, prev_low = high.shift(1), low.shift(1)

        higher_high = high > prev_high
        higher_low = low > prev_low
        lower_high = high < prev_high
        lower_low = low < prev_low

        # Structural break: close crosses beyond the prior 20-bar range —
        # trailing-only (no lookahead), unlike the centered fractal swing
        # points in app/features/structure/.
        prior_high_20 = prev_high.rolling(window=20, min_periods=20).max()
        prior_low_20 = prev_low.rolling(window=20, min_periods=20).min()
        bos_bullish = close > prior_high_20
        bos_bearish = close < prior_low_20
        break_of_structure = pd.Series(pd.NA, index=df.index, dtype="object")
        break_of_structure = break_of_structure.mask(bos_bullish, "bullish").mask(
            bos_bearish, "bearish"
        )

        inside_bar = (high < prev_high) & (low > prev_low)
        outside_bar = (high > prev_high) & (low < prev_low)
        gap_up = low > prev_high
        gap_down = high < prev_low

        bar_range = high - low
        nr4 = bar_range == bar_range.rolling(window=4, min_periods=4).min()
        nr7 = bar_range == bar_range.rolling(window=7, min_periods=7).min()

        return pd.DataFrame(
            {
                "higher_high": higher_high.fillna(False),
                "higher_low": higher_low.fillna(False),
                "lower_high": lower_high.fillna(False),
                "lower_low": lower_low.fillna(False),
                "break_of_structure": break_of_structure,
                "inside_bar": inside_bar.fillna(False),
                "outside_bar": outside_bar.fillna(False),
                "gap_up": gap_up.fillna(False),
                "gap_down": gap_down.fillna(False),
                "nr4": nr4.fillna(False),
                "nr7": nr7.fillna(False),
            },
            index=df.index,
        )
