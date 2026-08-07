"""Volatility features: ATR (+ expansion/contraction), Bollinger Bands, Keltner Channels, squeeze."""

import pandas as pd

from app.features import indicators


class VolatilityFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]

        atr14 = indicators.atr(high, low, close, 14)
        atr_avg20 = atr14.rolling(window=20, min_periods=20).mean()
        atr_expansion = atr14 > atr_avg20 * 1.2
        atr_contraction = atr14 < atr_avg20 * 0.8

        bb_upper, bb_middle, bb_lower = indicators.bollinger_bands(close, 20, 2.0)
        bb_width = (bb_upper - bb_lower) / bb_middle.replace(0, pd.NA) * 100

        kc_upper, kc_middle, kc_lower = indicators.keltner_channels(high, low, close, 20, 10, 2.0)

        # Classic "TTM squeeze" definition: Bollinger Bands contract inside
        # Keltner Channels when volatility is unusually compressed.
        volatility_squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)

        return pd.DataFrame(
            {
                "atr14": atr14,
                "atr_expansion": atr_expansion.fillna(False),
                "atr_contraction": atr_contraction.fillna(False),
                "bb_upper": bb_upper,
                "bb_middle": bb_middle,
                "bb_lower": bb_lower,
                "bb_width": bb_width,
                "kc_upper": kc_upper,
                "kc_middle": kc_middle,
                "kc_lower": kc_lower,
                "volatility_squeeze": volatility_squeeze.fillna(False),
            },
            index=df.index,
        )
