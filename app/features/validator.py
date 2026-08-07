"""Feature validation: bounds-clip known-range columns, scrub inf/NaN artifacts.

Unlike `app.data.validator` (which rejects whole bad candles), a single
out-of-range feature shouldn't invalidate the other ~60 columns computed for
that day — so this cleans in place rather than raising.
"""

import numpy as np
import pandas as pd

# (column, (lower_bound, upper_bound)) for features with a mathematically
# guaranteed range. Anything outside it is a computation artifact (e.g. a
# division near-zero), not a real reading.
_BOUNDED_COLUMNS: dict[str, tuple[float, float]] = {
    "rsi14": (0, 100),
    "adx14": (0, 100),
    "plus_di14": (0, 100),
    "minus_di14": (0, 100),
    "momentum_score": (-100, 100),
    "trend_strength": (0, 100),
    "accumulation_score": (0, 100),
    "distribution_score": (0, 100),
}


class FeatureValidator:
    @staticmethod
    def clean(df: pd.DataFrame) -> pd.DataFrame:
        cleaned = df.replace([np.inf, -np.inf], np.nan)
        for column, (lower, upper) in _BOUNDED_COLUMNS.items():
            if column in cleaned.columns:
                cleaned[column] = cleaned[column].clip(lower=lower, upper=upper)
        return cleaned
