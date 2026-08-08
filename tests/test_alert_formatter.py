"""Tests for app.alerts.formatter.AlertMessageFormatter."""

from datetime import datetime

from app.alerts.formatter import AlertMessageContext, AlertMessageFormatter


def _context(**overrides: object) -> AlertMessageContext:
    defaults: dict[str, object] = dict(
        symbol="TCS",
        score=91.0,
        quality="HIGH",
        breakout_level=842.0,
        feature_snapshot={
            "price": "845.5",
            "ema20": "820",
            "ema50": "800",
            "ema200": "750",
            "relative_volume": "2.8",
            "adx14": "31",
        },
        passed_rules=["resistance_proximity", "relative_volume", "trend", "adx"],
        timestamp=datetime(2026, 1, 5, 10, 42),
    )
    defaults.update(overrides)
    return AlertMessageContext(**defaults)  # type: ignore[arg-type]


def test_format_includes_real_values_only() -> None:
    text = AlertMessageFormatter.format_text(_context())
    assert "TCS" in text
    assert "845.50" in text
    assert "842.00" in text
    assert "91/100" in text
    assert "HIGH" in text
    assert "2.8x" in text
    assert "31" in text
    assert "10:42 IST" in text


def test_format_never_mentions_probability() -> None:
    text = AlertMessageFormatter.format_text(_context())
    assert "probability" not in text.lower()


def test_format_never_invents_entry_stop_target() -> None:
    text = AlertMessageFormatter.format_text(_context())
    for forbidden in ("stop loss", "stop-loss", "target price", "entry price"):
        assert forbidden not in text.lower()


def test_format_omits_missing_values_rather_than_inventing_them() -> None:
    context = _context(breakout_level=None, feature_snapshot={"price": "845.5"})
    text = AlertMessageFormatter.format_text(context)
    assert "Breakout Level" not in text
    assert "RVOL" not in text
    assert "ADX" not in text


def test_format_includes_disclaimer() -> None:
    text = AlertMessageFormatter.format_text(_context())
    assert "not an automatic trade" in text
