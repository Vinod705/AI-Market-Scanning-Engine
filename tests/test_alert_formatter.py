"""Tests for app.alerts.formatter.AlertMessageFormatter."""

from datetime import datetime

from app.alerts.formatter import AlertMessageContext, AlertMessageFormatter


def _context(**overrides: object) -> AlertMessageContext:
    defaults: dict[str, object] = dict(
        symbol="TCS",
        scanner_name="breakout_v1",
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


def _candidate_context(**overrides: object) -> AlertMessageContext:
    defaults: dict[str, object] = dict(
        symbol="NEWCO",
        scanner_name="fno_momentum_v1",
        score=74.0,
        quality="HIGH",
        breakout_level=None,
        feature_snapshot={
            "price": "312.5",
            "resistance_level": "300.0",
            "support_level": "280.0",
            "universe": "FNO",
            "setup_state": "MOMENTUM",
            "alert_category": "FNO_MOMENTUM",
            "fundamental_score": None,
            "technical_score": "78.5",
            "overall_score": "74.0",
            "data_completeness_pct": 0.0,
            "fundamental_reasons": [],
            "technical_reasons": ["Trend direction favorable", "RSI(14) favorable"],
            "risk_flags": ["Fundamental Score: UNKNOWN (no data source)"],
        },
        passed_rules=["setup_state_is_momentum_or_confirmed", "overall_score>=threshold"],
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


def test_candidate_format_shows_universe_and_setup_state() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    assert "NEWCO" in text
    assert "F&O MOMENTUM" in text
    assert "Universe: FNO" in text
    assert "Setup: MOMENTUM" in text


def test_candidate_format_reports_unknown_fundamental_score_honestly() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    assert "Fundamental Score: UNKNOWN (no data source)" in text
    assert "Technical Score: 78/100" in text
    assert "Overall Score: 74/100" in text


def test_candidate_format_shows_limited_fundamental_score_when_partial() -> None:
    context = _candidate_context(
        feature_snapshot={
            **_candidate_context().feature_snapshot,
            "fundamental_score": "62.0",
            "data_completeness_pct": 40.0,
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "Fundamental Score: 62/100 (LIMITED — 40% data)" in text


def test_candidate_format_never_invents_entry_stop_target() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    for forbidden in ("stop loss", "stop-loss", "target price", "entry price"):
        assert forbidden not in text.lower()


def test_candidate_format_mentions_score_is_not_probability() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    assert "not a probability" in text.lower()
