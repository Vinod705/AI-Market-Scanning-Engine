"""Synthetic OHLCV DataFrame builders shared across feature-calculator tests."""

import numpy as np
import pandas as pd


def make_trending_ohlcv(
    n: int = 260, start: float = 100.0, drift: float = 0.3, seed: int = 0
) -> pd.DataFrame:
    """A long random-walk-with-drift series — enough bars for EMA200 to warm up."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(drift, 1.0, n))
    close = np.maximum(close, 1.0)  # keep prices positive
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    volume = rng.integers(10_000, 100_000, n).astype(float)

    index = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )
