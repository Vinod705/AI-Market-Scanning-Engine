"""Tests for the per-category feature calculators in app/features/*/calculator.py."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.features.momentum.calculator import MomentumFeatureCalculator
from app.features.patterns.calculator import PatternFeatureCalculator
from app.features.price_action.calculator import PriceActionFeatureCalculator
from app.features.relative_strength.calculator import RelativeStrengthFeatureCalculator
from app.features.session.calculator import SessionFeatureCalculator
from app.features.structure.calculator import StructureFeatureCalculator
from app.features.support_resistance.calculator import SupportResistanceFeatureCalculator
from app.features.trend.calculator import TrendFeatureCalculator
from app.features.volatility.calculator import VolatilityFeatureCalculator
from app.features.volume.calculator import VolumeFeatureCalculator
from tests.ohlcv import make_trending_ohlcv


def test_trend_calculator_detects_golden_cross_in_uptrend() -> None:
    df = make_trending_ohlcv(n=260, drift=0.35)
    result = TrendFeatureCalculator.calculate(df)

    assert {"ema20", "ema50", "ema200", "sma20", "trend_direction", "trend_strength"} <= set(
        result.columns
    )
    assert result["ema200"].notna().any()
    assert bool(
        result["golden_cross"].any()
    )  # a persistent uptrend should cross ema50 above ema200 at some point
    assert set(result["trend_direction"].dropna().unique()) <= {"up", "down", "sideways"}


def test_momentum_calculator_columns_and_bounds() -> None:
    df = make_trending_ohlcv(n=60)
    result = MomentumFeatureCalculator.calculate(df)

    assert {
        "rsi14",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "adx14",
        "momentum_score",
    } <= set(result.columns)
    rsi = result["rsi14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()
    momentum = result["momentum_score"].dropna()
    assert (momentum >= -100).all() and (momentum <= 100).all()


def test_volatility_calculator_squeeze_flag_is_boolean() -> None:
    df = make_trending_ohlcv(n=60)
    result = VolatilityFeatureCalculator.calculate(df)

    assert {"atr14", "bb_upper", "bb_lower", "kc_upper", "kc_lower", "volatility_squeeze"} <= set(
        result.columns
    )
    assert result["volatility_squeeze"].dtype == bool


def test_volume_calculator_flags_spike() -> None:
    df = make_trending_ohlcv(n=40)
    df.loc[df.index[-1], "volume"] = df["volume"].iloc[:-1].mean() * 10  # inject an obvious spike
    result = VolumeFeatureCalculator.calculate(df)

    assert bool(result["volume_spike"].iloc[-1])
    assert not bool(result["volume_dry_up"].iloc[-1])


def test_price_action_gap_up_and_inside_bar() -> None:
    idx = pd.date_range("2026-01-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100, 105, 106],
            "high": [102, 108, 107],  # bar 2's high (107) < bar 1's high (108)
            "low": [
                98,
                106,
                106.5,
            ],  # bar 1 gaps up (low > prev high); bar 2's low (106.5) > bar 1's low (106)
            "close": [101, 107, 106.8],
            "volume": [1000, 1000, 500],
        },
        index=idx,
    )
    result = PriceActionFeatureCalculator.calculate(df)

    assert bool(result["gap_up"].iloc[1])
    assert bool(result["inside_bar"].iloc[2])  # bar 2's range is inside bar 1's


def test_price_action_nr4_flags_tightest_range() -> None:
    idx = pd.date_range("2026-01-01", periods=4)
    df = pd.DataFrame(
        {
            "open": [100, 100, 100, 100],
            "high": [110, 108, 106, 101],
            "low": [90, 92, 94, 99.5],
            "close": [100, 100, 100, 100],
            "volume": [1000, 1000, 1000, 1000],
        },
        index=idx,
    )
    result = PriceActionFeatureCalculator.calculate(df)
    assert bool(result["nr4"].iloc[3])  # last bar has the smallest range of the 4


def test_structure_calculator_columns_present() -> None:
    df = make_trending_ohlcv(n=60)
    result = StructureFeatureCalculator.calculate(df)
    assert {
        "swing_high", "swing_low", "trend_channel_upper", "trend_channel_lower",
        "is_range", "is_consolidation", "base_length_days", "range_width_pct",
    } <= set(result.columns)  # fmt: skip


def test_support_resistance_pivot_matches_formula() -> None:
    idx = pd.date_range("2026-01-01", periods=2)
    df = pd.DataFrame(
        {"open": [100, 100], "high": [110, 111], "low": [90, 91], "close": [100, 101]}, index=idx
    )
    result = SupportResistanceFeatureCalculator.calculate(df)
    expected_pivot = (110 + 90 + 100) / 3.0
    assert result["pivot_point"].iloc[1] == pytest.approx(expected_pivot)


def test_pattern_calculator_returns_all_boolean_columns() -> None:
    df = make_trending_ohlcv(n=100)
    result = PatternFeatureCalculator.calculate(df)
    expected_columns = {
        "pattern_triangle", "pattern_bull_flag", "pattern_bear_flag", "pattern_flat_base",
        "pattern_ipo_base", "pattern_rectangle", "pattern_cup_handle", "pattern_vcp",
    }  # fmt: skip
    assert expected_columns <= set(result.columns)
    for column in expected_columns:
        assert result[column].dtype == bool


def test_relative_strength_without_benchmark_is_all_nan() -> None:
    df = make_trending_ohlcv(n=30)
    result = RelativeStrengthFeatureCalculator.calculate(df, None)
    assert result["rs_vs_nifty"].isna().all()


def test_relative_strength_outperformance_is_positive() -> None:
    df = make_trending_ohlcv(n=40, drift=1.0, seed=1)
    benchmark = make_trending_ohlcv(n=40, drift=0.0, seed=2)
    result = RelativeStrengthFeatureCalculator.calculate(df, benchmark)
    assert result["rs_vs_nifty"].dropna().iloc[-1] > 0


def test_session_calculator_computes_opening_range_and_day_high_low() -> None:
    times = pd.date_range("2026-01-05 09:15", periods=120, freq="min")
    close = 100 + np.cumsum(np.random.default_rng(5).normal(0, 0.1, len(times)))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000,
        },
        index=times,
    )
    result = SessionFeatureCalculator.calculate(df)

    assert result.day_high is not None
    assert result.day_low is not None
    assert result.opening_range_high is not None
    assert result.session_vwap is not None
    assert result.day_high >= result.opening_range_high


def test_session_calculator_opening_range_uses_ist_market_open_not_utc() -> None:
    """Bug fix regression: market open (9:15) is IST, not UTC. Confirmed
    live this session — with UTC-stored intraday data (IntradayPrice.
    datetime's real storage convention), the old code localized "9:15"
    directly into UTC instead of converting real IST open (09:15 IST =
    03:45 UTC), so "opening range" was actually computed against
    09:15-09:30 UTC (14:45-15:00 IST). A real session_features row showed
    the signature: opening_range_high/low exactly equal to day_high/low,
    because the (wrongly-placed) window ended up covering the whole
    available session instead of the true first 15 minutes."""
    # Real market open in UTC: 09:15 IST = 03:45 UTC.
    times = pd.date_range("2026-01-05 03:45", periods=120, freq="min", tz="UTC")
    # Price rises steadily through the session, so day_high sits at the
    # very end — opening_range_high must reflect only the first 15
    # minutes (indices 0-14), which is far lower, if the window is
    # correctly placed at the real open.
    close = 100 + np.arange(len(times)) * 0.1
    df = pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05, "close": close, "volume": 1000},
        index=times,
    )
    result = SessionFeatureCalculator.calculate(df, market_timezone="Asia/Kolkata")

    assert result.opening_range_high is not None
    assert result.opening_range_high < Decimal("102")  # true first-15-min high ~= 101.45
    assert result.day_high is not None
    assert result.day_high > Decimal("111")  # true day high (last bar) ~= 111.95
    # The invariant the bug violated: a correctly-placed opening range
    # must be strictly narrower than the full day, not equal to it.
    assert result.opening_range_high < result.day_high


def test_session_calculator_naive_index_still_treated_as_market_local() -> None:
    """Naive (tz-less) input — e.g. test fixtures already expressed in
    market-local clock time — must behave exactly as before the fix: no
    tz conversion attempted, compared naive-to-naive."""
    times = pd.date_range("2026-01-05 09:15", periods=30, freq="min")  # naive, no tz
    close = 100 + np.arange(len(times)) * 0.1
    df = pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05, "close": close, "volume": 1000},
        index=times,
    )
    result = SessionFeatureCalculator.calculate(df, market_timezone="Asia/Kolkata")

    assert result.opening_range_high is not None
    assert result.opening_range_high < Decimal("102")  # first 15 minutes only
    assert result.day_high is not None
    assert result.day_high > Decimal("102")  # later bars go higher


def test_session_calculator_empty_returns_none() -> None:
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    result = SessionFeatureCalculator.calculate(empty_df)
    assert result.day_high is None
    assert result.session_vwap is None
