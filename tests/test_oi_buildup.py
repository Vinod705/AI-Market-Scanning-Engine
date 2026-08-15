"""Tests for app.derivatives.oi_buildup.classify — all 5 buildup categories."""

from decimal import Decimal

from app.derivatives.derivatives_models import BuildupClassification
from app.derivatives.oi_buildup import classify, percent_change


def test_long_buildup_when_price_and_oi_both_rise() -> None:
    assert classify(Decimal("5"), Decimal("1000")) == BuildupClassification.LONG_BUILDUP


def test_short_buildup_when_price_falls_and_oi_rises() -> None:
    assert classify(Decimal("-5"), Decimal("1000")) == BuildupClassification.SHORT_BUILDUP


def test_short_covering_when_price_rises_and_oi_falls() -> None:
    assert classify(Decimal("5"), Decimal("-1000")) == BuildupClassification.SHORT_COVERING


def test_long_unwinding_when_price_and_oi_both_fall() -> None:
    assert classify(Decimal("-5"), Decimal("-1000")) == BuildupClassification.LONG_UNWINDING


def test_neutral_when_price_flat() -> None:
    assert classify(Decimal("0"), Decimal("1000")) == BuildupClassification.NEUTRAL


def test_neutral_when_oi_flat() -> None:
    assert classify(Decimal("5"), Decimal("0")) == BuildupClassification.NEUTRAL


def test_neutral_when_price_change_missing() -> None:
    assert classify(None, Decimal("1000")) == BuildupClassification.NEUTRAL


def test_neutral_when_oi_change_missing() -> None:
    assert classify(Decimal("5"), None) == BuildupClassification.NEUTRAL


def test_neutral_when_both_missing() -> None:
    assert classify(None, None) == BuildupClassification.NEUTRAL


def test_percent_change_normal() -> None:
    assert percent_change(Decimal("110"), Decimal("100")) == Decimal("10")


def test_percent_change_none_when_no_previous() -> None:
    assert percent_change(Decimal("110"), None) is None


def test_percent_change_none_when_previous_is_zero() -> None:
    assert percent_change(Decimal("110"), Decimal("0")) is None
