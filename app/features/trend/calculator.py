"""Trend features: EMAs/SMA, trend direction & strength, golden/death cross."""

import numpy as np
import pandas as pd

from app.features import indicators


class TrendFeatureCalculator:
    """Computes trend features for a full OHLCV window, vectorized.

    `calculate` takes a DataFrame indexed by date with open/high/low/close/
    volume float columns and returns a DataFrame of trend feature columns on
    the same index — every category calculator in `app/features/` follows
    this same shape so `app/features/calculator.py` can just `pd.concat`
    them together.
    """

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]

        ema20 = indicators.ema(close, 20)
        ema50 = indicators.ema(close, 50)
        ema200 = indicators.ema(close, 200)
        sma20 = indicators.sma(close, 20)

        uptrend = (close > ema20) & (ema20 > ema50) & (ema50 > ema200)
        downtrend = (close < ema20) & (ema20 < ema50) & (ema50 < ema200)
        trend_direction = pd.Series(
            np.select([uptrend, downtrend], ["up", "down"], default="sideways"), index=df.index
        )

        # Fast/slow EMA spread as a % of price, scaled — a simple, self-contained
        # 0-100 measure of how stretched the trend is (no dependency on
        # momentum/ADX, kept in the momentum category instead).
        spread_pct = ((ema20 - ema200).abs() / close.replace(0, np.nan)) * 100
        trend_strength = (spread_pct * 10).clip(upper=100)

        ema50_above_200 = ema50 > ema200
        prev_ema50_above_200 = ema50_above_200.shift(1, fill_value=False)
        golden_cross = ema50_above_200 & ~prev_ema50_above_200
        death_cross = ~ema50_above_200 & prev_ema50_above_200

        return pd.DataFrame(
            {
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200,
                "sma20": sma20,
                "trend_direction": trend_direction,
                "trend_strength": trend_strength,
                "golden_cross": golden_cross,
                "death_cross": death_cross,
            },
            index=df.index,
        )
