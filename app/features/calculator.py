"""Combines every category calculator into the full daily feature set.

`FeatureEngine` is the only caller — it hands this a symbol's OHLCV window
(and, for relative strength, the benchmark's) and gets back one DataFrame
with every column `daily_features` has, ready to upsert.
"""

import pandas as pd

from app.features.momentum.calculator import MomentumFeatureCalculator
from app.features.patterns.calculator import PatternFeatureCalculator
from app.features.price_action.calculator import PriceActionFeatureCalculator
from app.features.relative_strength.calculator import RelativeStrengthFeatureCalculator
from app.features.structure.calculator import StructureFeatureCalculator
from app.features.support_resistance.calculator import SupportResistanceFeatureCalculator
from app.features.trend.calculator import TrendFeatureCalculator
from app.features.validator import FeatureValidator
from app.features.volatility.calculator import VolatilityFeatureCalculator
from app.features.volume.calculator import VolumeFeatureCalculator


class DailyFeatureCalculator:
    """Aggregates all nine daily-bar feature categories (session features are separate — see app/features/session/)."""

    @staticmethod
    def calculate(df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> pd.DataFrame:
        parts = [
            TrendFeatureCalculator.calculate(df),
            MomentumFeatureCalculator.calculate(df),
            VolatilityFeatureCalculator.calculate(df),
            VolumeFeatureCalculator.calculate(df),
            PriceActionFeatureCalculator.calculate(df),
            StructureFeatureCalculator.calculate(df),
            SupportResistanceFeatureCalculator.calculate(df),
            PatternFeatureCalculator.calculate(df),
            RelativeStrengthFeatureCalculator.calculate(df, benchmark_df),
        ]
        combined = pd.concat(parts, axis=1)
        return FeatureValidator.clean(combined)
