"""Tests for app.notifications.digest (pure functions, no DB/network)."""

from app.notifications.digest import (
    DigestEntry,
    build_digest_text,
    build_market_overview_text,
    split_by_universe,
)


def _entry(symbol: str, category: str | None, score: float) -> DigestEntry:
    return DigestEntry(symbol=symbol, alert_category=category, score=score)


def test_split_by_universe_separates_ipo_and_fno() -> None:
    entries = [
        _entry("EUROPRATIK", "IPO_PRE_BREAKOUT", 80.0),
        _entry("RELIANCE", "FNO_BREAKOUT", 90.0),
    ]
    ipo, fno = split_by_universe(entries)
    assert [e.symbol for e in ipo] == ["EUROPRATIK"]
    assert [e.symbol for e in fno] == ["RELIANCE"]


def test_split_by_universe_excludes_unrecognized_category() -> None:
    entries = [_entry("TCS", "breakout_v1", 70.0), _entry("INFY", None, 60.0)]
    ipo, fno = split_by_universe(entries)
    assert ipo == []
    assert fno == []


def test_split_by_universe_all_ipo_categories() -> None:
    entries = [
        _entry("A", "IPO_PRE_BREAKOUT", 1.0),
        _entry("B", "IPO_BREAKOUT", 2.0),
        _entry("C", "IPO_MOMENTUM", 3.0),
    ]
    ipo, fno = split_by_universe(entries)
    assert {e.symbol for e in ipo} == {"A", "B", "C"}
    assert fno == []


def test_split_by_universe_all_fno_categories() -> None:
    entries = [
        _entry("A", "FNO_PRE_BREAKOUT", 1.0),
        _entry("B", "FNO_BREAKOUT", 2.0),
        _entry("C", "FNO_MOMENTUM", 3.0),
    ]
    ipo, fno = split_by_universe(entries)
    assert {e.symbol for e in fno} == {"A", "B", "C"}
    assert ipo == []


def test_build_digest_text_returns_none_for_empty() -> None:
    assert build_digest_text([], universe_label="IPO") is None


def test_build_digest_text_sorts_by_score_descending() -> None:
    entries = [
        _entry("LOWSCORE", "IPO_BREAKOUT", 50.0),
        _entry("HIGHSCORE", "IPO_PRE_BREAKOUT", 95.5),
    ]
    text = build_digest_text(entries, universe_label="IPO")
    assert text is not None
    lines = text.splitlines()
    assert lines[0] == "\U0001f4ca IPO Breakout Digest — 2 new signal(s)"
    high_idx = next(i for i, line in enumerate(lines) if "HIGHSCORE" in line)
    low_idx = next(i for i, line in enumerate(lines) if "LOWSCORE" in line)
    assert high_idx < low_idx


def test_build_digest_text_formats_known_category_label() -> None:
    text = build_digest_text([_entry("EUROPRATIK", "IPO_PRE_BREAKOUT", 80.0)], universe_label="IPO")
    assert text is not None
    assert "EUROPRATIK — IPO PRE-BREAKOUT (score 80.0)" in text


def test_build_digest_text_falls_back_to_raw_category_when_unlabeled() -> None:
    text = build_digest_text([_entry("XYZ", "SOME_NEW_CATEGORY", 10.0)], universe_label="F&O")
    assert text is not None
    assert "SOME_NEW_CATEGORY" in text


def test_build_digest_text_handles_missing_category() -> None:
    text = build_digest_text([_entry("XYZ", None, 10.0)], universe_label="F&O")
    assert text is not None
    assert "UNKNOWN" in text


def test_build_market_overview_returns_none_when_nothing_to_say() -> None:
    text = build_market_overview_text(
        regime=None,
        regime_score=None,
        sector_rotation_counts={},
        momentum_trigger_count=0,
        lookback_hours=9.0,
    )
    assert text is None


def test_build_market_overview_shows_regime_and_score() -> None:
    text = build_market_overview_text(
        regime="SUPPORTIVE",
        regime_score=78.5,
        sector_rotation_counts={},
        momentum_trigger_count=0,
        lookback_hours=9.0,
    )
    assert text is not None
    assert "Regime: SUPPORTIVE (score 78.5)" in text


def test_build_market_overview_reports_no_snapshot_yet_when_regime_missing() -> None:
    text = build_market_overview_text(
        regime=None,
        regime_score=None,
        sector_rotation_counts={},
        momentum_trigger_count=3,
        lookback_hours=9.0,
    )
    assert text is not None
    assert "Regime: no snapshot yet" in text


def test_build_market_overview_includes_sector_counts() -> None:
    text = build_market_overview_text(
        regime="NEUTRAL",
        regime_score=50.0,
        sector_rotation_counts={"LEADING": 2, "LAGGING": 1},
        momentum_trigger_count=0,
        lookback_hours=9.0,
    )
    assert text is not None
    assert "Sectors:" in text
    assert "2 leading" in text
    assert "1 lagging" in text


def test_build_market_overview_includes_momentum_trigger_count() -> None:
    text = build_market_overview_text(
        regime="SUPPORTIVE",
        regime_score=80.0,
        sector_rotation_counts={},
        momentum_trigger_count=7,
        lookback_hours=9.0,
    )
    assert text is not None
    assert "Momentum triggers: 7" in text
