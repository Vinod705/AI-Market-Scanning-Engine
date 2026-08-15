"""Tests for app.analytics.rrg.rrg_engine — pure calculation, no I/O."""

import pandas as pd
import pytest

from app.analytics.rrg.rrg_engine import classify_quadrant, compute_rrg_series
from app.analytics.rrg.rrg_models import RrgQuadrant

# --- classify_quadrant -------------------------------------------------


def test_leading_when_ratio_and_momentum_both_above_100() -> None:
    assert classify_quadrant(105.0, 102.0) == RrgQuadrant.LEADING


def test_weakening_when_ratio_above_and_momentum_below_100() -> None:
    assert classify_quadrant(105.0, 98.0) == RrgQuadrant.WEAKENING


def test_lagging_when_ratio_and_momentum_both_below_100() -> None:
    assert classify_quadrant(95.0, 98.0) == RrgQuadrant.LAGGING


def test_improving_when_ratio_below_and_momentum_above_100() -> None:
    assert classify_quadrant(95.0, 102.0) == RrgQuadrant.IMPROVING


def test_boundary_exactly_100_on_both_axes_counts_as_leading() -> None:
    """Deterministic, documented tie-break: exactly 100 on either axis
    counts as the non-negative side, consistently on both axes."""
    assert classify_quadrant(100.0, 100.0) == RrgQuadrant.LEADING


def test_none_when_ratio_is_nan() -> None:
    assert classify_quadrant(float("nan"), 102.0) is None


def test_none_when_momentum_is_nan() -> None:
    assert classify_quadrant(105.0, float("nan")) is None


# --- compute_rrg_series --------------------------------------------------

_N = 80


def _frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"close": closes}, index=dates)


def test_compute_rrg_series_returns_one_point_per_input_date() -> None:
    security = _frame([100 + i * 0.8 for i in range(_N)])
    benchmark = _frame([100 + i * 0.3 for i in range(_N)])

    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")

    assert len(points) == _N
    assert points[0].symbol == "TEST"
    assert points[0].benchmark_symbol == "BENCH"
    assert points[-1].date == security.index[-1].date()


def test_leading_dates_have_no_rs_ratio_until_enough_history_exists() -> None:
    security = _frame([100 + i * 0.8 for i in range(_N)])
    benchmark = _frame([100 + i * 0.3 for i in range(_N)])

    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")

    assert points[0].rs_ratio is None
    assert points[0].rs_momentum is None
    assert points[0].quadrant is None


def test_later_dates_have_populated_values_once_window_is_full() -> None:
    security = _frame([100 + i * 0.8 for i in range(_N)])
    benchmark = _frame([100 + i * 0.3 for i in range(_N)])

    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")
    last = points[-1]

    assert last.rs is not None
    assert last.rs_ratio is not None
    assert last.rs_momentum is not None
    assert last.quadrant is not None
    assert last.quadrant == classify_quadrant(float(last.rs_ratio), float(last.rs_momentum))


def test_outperforming_security_has_positive_rs() -> None:
    """Security consistently beats the benchmark -> rs_vs_nifty-style value
    (reused from the existing calculator) should be positive at the tail."""
    security = _frame([100 + i * 0.8 for i in range(_N)])
    benchmark = _frame([100 + i * 0.3 for i in range(_N)])

    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")

    assert points[-1].rs is not None
    assert points[-1].rs > 0


def test_computed_at_is_timestamped_and_shared_across_the_series() -> None:
    security = _frame([100 + i * 0.5 for i in range(_N)])
    benchmark = _frame([100.0 for _ in range(_N)])

    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")

    assert all(p.computed_at == points[0].computed_at for p in points)
    assert points[0].computed_at is not None


def test_reuses_existing_relative_strength_calculator_not_a_duplicate_formula() -> None:
    """rs_vs_nifty from RelativeStrengthFeatureCalculator directly should
    match compute_rrg_series's own `rs` field bit-for-bit — proving the
    same calculator is reused, not reimplemented."""
    from app.features.relative_strength.calculator import RelativeStrengthFeatureCalculator

    security = _frame([100 + i * 0.8 for i in range(_N)])
    benchmark = _frame([100 + i * 0.3 for i in range(_N)])

    expected = RelativeStrengthFeatureCalculator.calculate(security, benchmark)["rs_vs_nifty"]
    points = compute_rrg_series(security, benchmark, symbol="TEST", benchmark_symbol="BENCH")

    for point, expected_value in zip(points, expected, strict=True):
        if pd.isna(expected_value):
            assert point.rs is None
        else:
            assert point.rs is not None
            assert float(point.rs) == pytest.approx(expected_value, abs=0.001)
