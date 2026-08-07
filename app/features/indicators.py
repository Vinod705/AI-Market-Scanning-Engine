"""Shared, vectorized technical-analysis math.

Every category calculator builds on these functions instead of re-deriving
formulas — the one place that needs to be correct for EMA/RSI/ATR/ADX/etc.
Pure pandas/numpy: no TA-Lib or pandas-ta dependency, so no C-toolchain or
numpy-2.x compatibility risk. All functions take/return `pandas.Series`
(or a tuple of them) aligned to the input index and operate on `float64` —
callers are responsible for converting to/from `Decimal` at the DB
boundary.

Where a formula has more than one common convention (RSI, ATR, ADX), this
uses Wilder's original smoothing (`ewm(alpha=1/period, adjust=False)`),
the convention most charting platforms default to.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _wilder_smooth(true_range(high, low, close), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.where(avg_loss != 0, 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (adx, plus_di, minus_di), Wilder's original method."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    atr_value = atr(high, low, close, period)
    plus_di = 100 * _wilder_smooth(plus_dm, period) / atr_value.replace(0, np.nan)
    minus_di = 100 * _wilder_smooth(minus_dm, period) / atr_value.replace(0, np.nan)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_value = _wilder_smooth(dx.fillna(0), period)

    return adx_value, plus_di, minus_di


def bollinger_bands(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower)."""
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    return middle + num_std * std, middle, middle - num_std * std


def keltner_channels(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower)."""
    middle = ema(close, period)
    atr_value = atr(high, low, close, atr_period)
    return middle + multiplier * atr_value, middle, middle - multiplier * atr_value


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def typical_price(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return (high + low + close) / 3.0


def swing_points(high: pd.Series, low: pd.Series, window: int = 2) -> tuple[pd.Series, pd.Series]:
    """Fractal swing points: a bar is a swing high/low if it's the extreme
    within `window` bars on both sides. Returns (is_swing_high, is_swing_low)
    boolean Series; the trailing/leading `window` bars can't be evaluated
    (no full neighborhood) and are False.
    """
    span = 2 * window + 1
    rolling_max = high.rolling(window=span, center=True, min_periods=span).max()
    rolling_min = low.rolling(window=span, center=True, min_periods=span).min()
    is_swing_high = (high == rolling_max).fillna(False)
    is_swing_low = (low == rolling_min).fillna(False)
    return is_swing_high, is_swing_low


def floor_pivot(prev_high: float, prev_low: float, prev_close: float) -> float:
    """Classic floor-trader pivot point from the prior bar's H/L/C."""
    return (prev_high + prev_low + prev_close) / 3.0
