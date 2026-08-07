"""Reference-value tests for app.features.indicators."""

import numpy as np
import pandas as pd
import pytest

from app.features import indicators


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values)))


def test_sma_basic() -> None:
    result = indicators.sma(_series([1, 2, 3, 4, 5]), period=3)
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)
    assert pd.isna(result.iloc[1])


def test_ema_converges_to_constant_series() -> None:
    result = indicators.ema(_series([10.0] * 30), span=10)
    assert result.iloc[-1] == pytest.approx(10.0)


def test_rsi_all_gains_is_100() -> None:
    close = _series([float(i) for i in range(1, 30)])  # strictly increasing
    result = indicators.rsi(close, period=14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero() -> None:
    close = _series([float(i) for i in range(30, 1, -1)])  # strictly decreasing
    result = indicators.rsi(close, period=14)
    assert result.iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_bounded_0_100() -> None:
    rng = np.random.default_rng(42)
    close = _series(list(100 + np.cumsum(rng.normal(0, 1, 60))))
    result = indicators.rsi(close, period=14).dropna()
    assert (result >= 0).all()
    assert (result <= 100).all()


def test_atr_non_negative() -> None:
    idx = pd.date_range("2026-01-01", periods=30)
    high = pd.Series(np.arange(30) + 101.0, index=idx)
    low = pd.Series(np.arange(30) + 99.0, index=idx)
    close = pd.Series(np.arange(30) + 100.0, index=idx)
    result = indicators.atr(high, low, close, period=14).dropna()
    assert (result > 0).all()


def test_macd_histogram_equals_line_minus_signal() -> None:
    rng = np.random.default_rng(1)
    close = _series(list(100 + np.cumsum(rng.normal(0, 1, 60))))
    macd_line, signal_line, histogram = indicators.macd(close)
    diff = (histogram - (macd_line - signal_line)).dropna()
    assert (diff.abs() < 1e-9).all()


def test_adx_bounded_and_shapes() -> None:
    idx = pd.date_range("2026-01-01", periods=60)
    rng = np.random.default_rng(2)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)), index=idx)
    high = close + 1
    low = close - 1
    adx, plus_di, minus_di = indicators.adx(high, low, close, period=14)
    assert len(adx) == len(close)
    valid_adx = adx.dropna()
    assert (valid_adx >= 0).all()
    assert (valid_adx <= 100).all()


def test_bollinger_bands_ordering() -> None:
    rng = np.random.default_rng(3)
    close = _series(list(100 + np.cumsum(rng.normal(0, 1, 40))))
    upper, middle, lower = indicators.bollinger_bands(close, period=20)
    valid = pd.DataFrame({"u": upper, "m": middle, "l": lower}).dropna()
    assert (valid["u"] >= valid["m"]).all()
    assert (valid["m"] >= valid["l"]).all()


def test_keltner_channels_ordering() -> None:
    idx = pd.date_range("2026-01-01", periods=40)
    rng = np.random.default_rng(4)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 40)), index=idx)
    high, low = close + 1, close - 1
    upper, middle, lower = indicators.keltner_channels(high, low, close, period=20, atr_period=10)
    valid = pd.DataFrame({"u": upper, "m": middle, "l": lower}).dropna()
    assert (valid["u"] >= valid["m"]).all()
    assert (valid["m"] >= valid["l"]).all()


def test_obv_increases_on_up_days() -> None:
    close = _series([10, 11, 12, 11, 13])
    volume = pd.Series([100, 100, 100, 100, 100], index=close.index)
    result = indicators.obv(close, volume)
    assert result.iloc[1] > result.iloc[0]  # up day adds volume
    assert result.iloc[3] < result.iloc[2]  # down day subtracts volume


def test_swing_points_detects_known_peak() -> None:
    idx = pd.date_range("2026-01-01", periods=7)
    high = pd.Series([10, 11, 12, 20, 12, 11, 10], index=idx)
    low = high - 1
    is_high, is_low = indicators.swing_points(high, low, window=2)
    assert is_high.iloc[3]  # the spike at position 3 is a swing high
    assert not is_high.iloc[0]
    assert not is_high.iloc[-1]


def test_floor_pivot_formula() -> None:
    assert indicators.floor_pivot(prev_high=110, prev_low=90, prev_close=100) == pytest.approx(
        100.0
    )


def test_typical_price() -> None:
    idx = pd.date_range("2026-01-01", periods=3)
    high = pd.Series([12, 12, 12], index=idx)
    low = pd.Series([9, 9, 9], index=idx)
    close = pd.Series([10.5, 10.5, 10.5], index=idx)
    result = indicators.typical_price(high, low, close)
    assert result.iloc[0] == pytest.approx((12 + 9 + 10.5) / 3)
