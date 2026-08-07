"""Volume features: MA, relative volume, OBV, spikes/dry-ups, accumulation/distribution score."""

import numpy as np
import pandas as pd

from app.features import indicators


class VolumeFeatureCalculator:
    """See `TrendFeatureCalculator` for the shared calculate(df) -> DataFrame contract."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close, volume = df["high"], df["low"], df["close"], df["volume"]

        volume_ma20 = indicators.sma(volume, 20)
        relative_volume = volume / volume_ma20.replace(0, np.nan)
        obv = indicators.obv(close, volume)

        volume_spike = relative_volume > 2.0
        volume_dry_up = relative_volume < 0.5

        # Money-flow-volume split into accumulation (buying pressure) and
        # distribution (selling pressure), a la the Accumulation/Distribution
        # Line, but reported as two independent 0-100 scores (share of the
        # rolling 20-day volume attributable to each) rather than one signed
        # cumulative line.
        range_ = (high - low).replace(0, np.nan)
        money_flow_multiplier = ((close - low) - (high - close)) / range_
        money_flow_volume = money_flow_multiplier.fillna(0) * volume

        rolling_volume_sum = volume.rolling(window=20, min_periods=20).sum().replace(0, np.nan)
        accumulation_score = (
            money_flow_volume.clip(lower=0).rolling(window=20, min_periods=20).sum()
            / rolling_volume_sum
            * 100
        ).clip(0, 100)
        distribution_score = (
            (-money_flow_volume).clip(lower=0).rolling(window=20, min_periods=20).sum()
            / rolling_volume_sum
            * 100
        ).clip(0, 100)

        return pd.DataFrame(
            {
                "volume_ma20": volume_ma20,
                "relative_volume": relative_volume,
                "obv": obv,
                "volume_spike": volume_spike.fillna(False),
                "volume_dry_up": volume_dry_up.fillna(False),
                "accumulation_score": accumulation_score,
                "distribution_score": distribution_score,
            },
            index=df.index,
        )
