"""Tests for app.analytics.sector.sector_breadth — pure math, no I/O."""

from decimal import Decimal

import pytest

from app.analytics.sector.sector_breadth import compute_breadth, compute_volume_participation


def test_breadth_all_advancing() -> None:
    assert compute_breadth(40, 40) == Decimal("100")


def test_breadth_none_advancing() -> None:
    assert compute_breadth(0, 40) == Decimal("0")


def test_breadth_half_advancing() -> None:
    assert compute_breadth(20, 40) == Decimal("50")


def test_breadth_none_when_no_constituents() -> None:
    assert compute_breadth(0, 0) is None


def test_breadth_rejects_advancing_count_above_total() -> None:
    with pytest.raises(ValueError, match="between 0 and total_count"):
        compute_breadth(41, 40)


def test_breadth_rejects_negative_advancing_count() -> None:
    with pytest.raises(ValueError, match="between 0 and total_count"):
        compute_breadth(-1, 40)


def test_volume_participation_all_above_average() -> None:
    assert compute_volume_participation(25, 25) == Decimal("100")


def test_volume_participation_partial() -> None:
    assert compute_volume_participation(5, 20) == Decimal("25")


def test_volume_participation_none_when_no_constituents() -> None:
    assert compute_volume_participation(0, 0) is None


def test_volume_participation_rejects_count_above_total() -> None:
    with pytest.raises(ValueError, match="between 0 and total_count"):
        compute_volume_participation(21, 20)
