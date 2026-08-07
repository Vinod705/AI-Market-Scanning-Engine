"""Momentum features: RSI, MACD, ADX/+DI/-DI, and a composite momentum score."""

import numpy as np
import pandas as pd

from app.features import indicators


class MomentumFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]

        rsi14 = indicators.rsi(close, 14)
        macd_line, macd_signal, macd_histogram = indicators.macd(close)
        adx14, plus_di14, minus_di14 = indicators.adx(high, low, close, 14)

        # Composite -100..+100 momentum score: half from RSI distance from the
        # neutral 50 line, half from MACD histogram normalized by price. This
        # isn't a standard named indicator — it's a documented heuristic blend
        # so scanners have a single sortable momentum number.
        macd_hist_pct = (macd_histogram / close.replace(0, np.nan)) * 100
        momentum_score = (0.5 * (rsi14 - 50) * 2 + 0.5 * (macd_hist_pct * 50).clip(-100, 100)).clip(
            -100, 100
        )

        return pd.DataFrame(
            {
                "rsi14": rsi14,
                "macd_line": macd_line,
                "macd_signal": macd_signal,
                "macd_histogram": macd_histogram,
                "adx14": adx14,
                "plus_di14": plus_di14,
                "minus_di14": minus_di14,
                "momentum_score": momentum_score,
            },
            index=df.index,
        )
