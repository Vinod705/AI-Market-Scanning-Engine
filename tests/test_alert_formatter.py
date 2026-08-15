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
            "scanner_sources": ["5PAISA"],
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


def test_candidate_format_shows_fundamental_data_source() -> None:
    context = _candidate_context(
        feature_snapshot={
            **_candidate_context().feature_snapshot,
            "fundamental_score": "62.0",
            "data_completeness_pct": 40.0,
            "fundamental_field_sources": [
                {
                    "field_name": "pe",
                    "value": 24.0,
                    "source": "Trendlyne",
                    "period": "TTM",
                    "status": "AVAILABLE",
                },
                {
                    "field_name": "roe_pct",
                    "value": 8.9,
                    "source": "Trendlyne",
                    "period": "Annual",
                    "status": "AVAILABLE",
                },
            ],
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "Fundamental Data Source(s): Trendlyne" in text


def test_candidate_format_omits_data_source_line_when_fundamental_unknown() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    assert "Fundamental Data Source(s)" not in text


def test_candidate_format_shows_scanner_sources() -> None:
    text = AlertMessageFormatter.format_text(_candidate_context())
    assert "Scanner Sources: 5PAISA" in text


def _momentum_context(**overrides: object) -> AlertMessageContext:
    defaults: dict[str, object] = dict(
        symbol="ABC",
        scanner_name="momentum_state_v1",
        score=87.0,
        quality="HIGH",
        breakout_level=None,
        feature_snapshot={
            "momentum_state": "TRIGGERED",
            "from_state": "ACTIVATING",
            "reason": "score 87.00 reached the trigger band",
            "date": "2026-01-05",
            "evidence": {
                "overall_score": 87.0,
                "confidence": 70.0,
                "positive_factors": ["Technical: RSI(14) favorable"],
                "negative_factors": ["Market regime: RISK_OFF"],
                "missing_data": ["news"],
                "component_scores": {
                    "technical": {
                        "status": "AVAILABLE",
                        "score": 82.0,
                        "weight": 25.0,
                        "reasons": [
                            "Technical: Trend direction favorable",
                            "Technical: Price vs session VWAP favorable",
                        ],
                    },
                    "volume": {
                        "status": "AVAILABLE",
                        "score": 100.0,
                        "weight": 15.0,
                        "reasons": ["Volume: relative_volume=3.10x average"],
                    },
                    "oi": {
                        "status": "AVAILABLE",
                        "score": 75.0,
                        "weight": 15.0,
                        "reasons": ["OI: futures LONG_BUILDUP"],
                    },
                    "sector_rrg": {
                        "status": "AVAILABLE",
                        "score": 86.0,
                        "weight": 15.0,
                        "reasons": ["Sector/RRG: LEADING vs NIFTY50"],
                    },
                    "market_regime": {
                        "status": "AVAILABLE",
                        "score": 30.0,
                        "weight": 10.0,
                        "reasons": ["Market regime: RISK_OFF"],
                    },
                    "fundamentals": {
                        "status": "MISSING",
                        "score": None,
                        "weight": 10.0,
                        "reasons": ["no cached fundamental snapshot yet"],
                    },
                    "news": {
                        "status": "MISSING",
                        "score": None,
                        "weight": 10.0,
                        "reasons": ["no NewsProvider supplied (live-call opt-in)"],
                    },
                },
            },
        },
        passed_rules=["score 87.00 reached the trigger band"],
        timestamp=datetime(2026, 1, 5, 10, 42),
    )
    defaults.update(overrides)
    return AlertMessageContext(**defaults)  # type: ignore[arg-type]


def test_momentum_format_shows_header_symbol_and_score() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "MOMENTUM TRIGGER" in text
    assert "ABC" in text
    assert "Score: 87/100" in text
    assert "State: TRIGGERED (from ACTIVATING)" in text


def test_momentum_format_confirmed_state_uses_confirmed_header() -> None:
    context = _momentum_context(
        feature_snapshot={
            **_momentum_context().feature_snapshot,
            "momentum_state": "CONFIRMED",
            "from_state": "TRIGGERED",
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "MOMENTUM CONFIRMED" in text
    assert "MOMENTUM TRIGGER" not in text


def test_momentum_format_renders_available_sections_with_stripped_reasons() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "TECHNICAL" in text
    assert "- Trend direction favorable" in text
    assert "VOLUME" in text
    assert "- relative_volume=3.10x average" in text
    assert "OI" in text
    assert "- futures LONG_BUILDUP" in text
    assert "SECTOR" in text
    assert "- LEADING vs NIFTY50" in text
    assert "MARKET" in text


def test_momentum_format_omits_missing_components_entirely() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "FUNDAMENTALS" not in text
    assert "NEWS" not in text


def test_momentum_format_why_now_lists_only_contributing_components() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "WHY NOW" in text
    why_now_line = text.splitlines()[text.splitlines().index("WHY NOW") + 1]
    assert "Technical" in why_now_line
    assert "Volume" in why_now_line
    assert "OI" in why_now_line
    assert "Sector" in why_now_line
    # market_regime scored 30 (< 50) so it must not be counted as contributing
    assert "Market" not in why_now_line


def test_momentum_format_shows_risks_from_negative_factors() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "RISKS" in text
    assert "- RISK_OFF" in text


def test_momentum_format_omits_risks_section_when_no_negative_factors() -> None:
    context = _momentum_context(
        feature_snapshot={
            **_momentum_context().feature_snapshot,
            "evidence": {
                **_momentum_context().feature_snapshot["evidence"],  # type: ignore[index]
                "negative_factors": [],
            },
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "RISKS" not in text


def test_momentum_format_shows_confidence_and_disclaimer() -> None:
    text = AlertMessageFormatter.format_text(_momentum_context())
    assert "Confidence: 70%" in text
    assert "not an automatic trade" in text
    assert "not a probability" in text.lower()


def test_momentum_format_first_observation_has_no_from_state() -> None:
    context = _momentum_context(
        feature_snapshot={**_momentum_context().feature_snapshot, "from_state": None}
    )
    text = AlertMessageFormatter.format_text(context)
    assert "State: TRIGGERED" in text
    assert "(from" not in text


def test_candidate_format_shows_both_scanner_sources_when_confirmed() -> None:
    context = _candidate_context(
        feature_snapshot={
            **_candidate_context().feature_snapshot,
            "scanner_sources": ["5PAISA", "TRADINGVIEW"],
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "Scanner Sources: 5PAISA, TRADINGVIEW" in text


def test_candidate_format_defaults_scanner_sources_when_missing() -> None:
    """Pre-Phase-7 snapshots have no scanner_sources key at all."""
    context = _candidate_context(
        feature_snapshot={
            k: v for k, v in _candidate_context().feature_snapshot.items() if k != "scanner_sources"
        }
    )
    text = AlertMessageFormatter.format_text(context)
    assert "Scanner Sources: 5PAISA" in text
