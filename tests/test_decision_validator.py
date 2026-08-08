"""Tests for app.decision.validator.DecisionValidator."""

from datetime import date

from app.decision.validator import DecisionValidator


def test_missing_features_returns_only_missing_keys() -> None:
    snapshot = {"price": "100", "ema20": None, "ema50": "None"}
    missing = DecisionValidator.missing_features(snapshot, ["price", "ema20", "ema50", "adx14"])
    assert missing == ["ema20", "ema50", "adx14"]


def test_is_fresh_within_window() -> None:
    assert (
        DecisionValidator.is_fresh(date(2026, 1, 4), max_age_days=1, today=date(2026, 1, 5)) is True
    )


def test_is_fresh_outside_window() -> None:
    assert (
        DecisionValidator.is_fresh(date(2026, 1, 1), max_age_days=1, today=date(2026, 1, 5))
        is False
    )


def test_as_float_parses_string() -> None:
    assert DecisionValidator.as_float({"adx14": "24.81"}, "adx14") == 24.81


def test_as_float_returns_none_for_missing() -> None:
    assert DecisionValidator.as_float({}, "adx14") is None
    assert DecisionValidator.as_float({"adx14": None}, "adx14") is None


def test_as_float_returns_none_for_invalid() -> None:
    assert DecisionValidator.as_float({"adx14": "not-a-number"}, "adx14") is None
