"""Tests for app.decision.rules — Decision Rules v1, evaluated individually."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.decision.models import DecisionCandidate, RuleStatus
from app.decision.rules import (
    check_adx,
    check_data_freshness,
    check_market_session,
    check_minimum_score,
    check_relative_volume,
    check_required_features,
    check_resistance_proximity,
    check_trend,
)

_GOOD_SNAPSHOT: dict[str, object] = {
    "price": "110",
    "ema20": "105",
    "ema50": "100",
    "ema200": "90",
    "adx14": "30",
    "relative_volume": "2.5",
    "resistance_level": "112",
    "breakout_level": "112",
}

_MONDAY_MARKET_OPEN = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
_MONDAY_AFTER_CLOSE = datetime(2026, 1, 5, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _candidate(
    *, score: float = 85.0, scan_date: date = date(2026, 1, 5), **snapshot_overrides: object
) -> DecisionCandidate:
    snapshot = {**_GOOD_SNAPSHOT, **snapshot_overrides}
    return DecisionCandidate(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        score=score,
        scan_date=scan_date,
        feature_snapshot=snapshot,
    )


def _settings() -> Settings:
    return Settings()


def test_required_features_passes_when_all_present() -> None:
    result = check_required_features(_candidate(), _settings())
    assert result.status == RuleStatus.PASS


def test_required_features_fails_when_missing() -> None:
    result = check_required_features(_candidate(ema200=None), _settings())
    assert result.status == RuleStatus.FAIL
    assert "ema200" in result.reason


def test_data_freshness_passes_for_today() -> None:
    result = check_data_freshness(
        _candidate(scan_date=date(2026, 1, 5)), _settings(), now=_MONDAY_MARKET_OPEN
    )
    assert result.status == RuleStatus.PASS


def test_data_freshness_fails_when_stale() -> None:
    settings = Settings(decision_max_data_age_days=1)
    result = check_data_freshness(_candidate(scan_date=date(2020, 1, 1)), settings)
    assert result.status == RuleStatus.FAIL


def test_data_freshness_uses_feature_date_when_present_and_fresh() -> None:
    """fno_momentum_v1/pre_breakout_v1/ipo_intraday_v1 stash the real
    underlying-feature date in feature_snapshot['feature_date'] (see
    app.candidates.builder) — when it's fresh, the rule passes and
    reports against that date, not scan_date."""
    candidate = _candidate(scan_date=date(2026, 1, 5), feature_date="2026-01-05")
    result = check_data_freshness(candidate, _settings(), now=_MONDAY_MARKET_OPEN)
    assert result.status == RuleStatus.PASS
    assert result.actual_value == "2026-01-05"
    assert "underlying feature data" in result.reason


def test_data_freshness_fails_when_feature_date_stale_even_if_scan_date_fresh() -> None:
    """The real gap this closes: a fresh scanner run (scan_date=today)
    that scored genuinely stale technical features must NOT read as
    fresh just because the scanner itself ran today."""
    settings = Settings(decision_max_data_age_days=1)
    candidate = _candidate(scan_date=date(2026, 1, 5), feature_date="2020-01-01")
    result = check_data_freshness(candidate, settings, now=_MONDAY_MARKET_OPEN)
    assert result.status == RuleStatus.FAIL
    assert result.actual_value == "2020-01-01"
    assert "underlying feature data" in result.reason


def test_data_freshness_falls_back_to_scan_date_when_feature_date_missing() -> None:
    """LISTED scanners (breakout_v1/vcp_v1/momentum_v1/orb_v1) never set
    feature_date — scan_date already *is* the feature date for those, so
    existing behavior must be unchanged."""
    candidate = _candidate(scan_date=date(2026, 1, 5))
    assert "feature_date" not in candidate.feature_snapshot
    result = check_data_freshness(candidate, _settings(), now=_MONDAY_MARKET_OPEN)
    assert result.status == RuleStatus.PASS
    assert result.actual_value == "2026-01-05"
    assert "scanner result" in result.reason


def test_data_freshness_falls_back_to_scan_date_when_feature_date_unparseable() -> None:
    """A malformed feature_date must degrade to the existing fallback,
    never raise or silently pass."""
    candidate = _candidate(scan_date=date(2026, 1, 5), feature_date="not-a-date")
    result = check_data_freshness(candidate, _settings(), now=_MONDAY_MARKET_OPEN)
    assert result.status == RuleStatus.PASS
    assert result.actual_value == "2026-01-05"
    assert "scanner result" in result.reason


def test_minimum_score_passes_above_threshold() -> None:
    settings = Settings(decision_min_alert_score=80.0)
    result = check_minimum_score(_candidate(score=85.0), settings)
    assert result.status == RuleStatus.PASS


def test_minimum_score_fails_below_threshold() -> None:
    settings = Settings(decision_min_alert_score=80.0)
    result = check_minimum_score(_candidate(score=70.0), settings)
    assert result.status == RuleStatus.FAIL


def test_trend_passes_when_ema_stack_aligned() -> None:
    result = check_trend(_candidate(), _settings())
    assert result.status == RuleStatus.PASS


def test_trend_fails_when_ema_stack_not_aligned() -> None:
    result = check_trend(_candidate(ema50="106"), _settings())
    assert result.status == RuleStatus.FAIL


def test_relative_volume_passes_above_threshold() -> None:
    settings = Settings(decision_min_rvol=2.0)
    result = check_relative_volume(_candidate(relative_volume="2.5"), settings)
    assert result.status == RuleStatus.PASS


def test_relative_volume_fails_below_threshold() -> None:
    settings = Settings(decision_min_rvol=2.0)
    result = check_relative_volume(_candidate(relative_volume="1.0"), settings)
    assert result.status == RuleStatus.FAIL


def test_adx_passes_above_threshold() -> None:
    settings = Settings(decision_min_adx=25.0)
    result = check_adx(_candidate(adx14="30"), settings)
    assert result.status == RuleStatus.PASS


def test_adx_fails_below_threshold() -> None:
    settings = Settings(decision_min_adx=25.0)
    result = check_adx(_candidate(adx14="10"), settings)
    assert result.status == RuleStatus.FAIL


def test_resistance_proximity_passes_within_distance() -> None:
    settings = Settings(decision_resistance_distance_percent=3.0)
    result = check_resistance_proximity(_candidate(price="110", resistance_level="112"), settings)
    assert result.status == RuleStatus.PASS


def test_resistance_proximity_fails_when_too_far() -> None:
    settings = Settings(decision_resistance_distance_percent=3.0)
    result = check_resistance_proximity(_candidate(price="110", resistance_level="200"), settings)
    assert result.status == RuleStatus.FAIL


def test_market_session_passes_when_open() -> None:
    result = check_market_session(_candidate(), _settings(), now=_MONDAY_MARKET_OPEN)
    assert result.status == RuleStatus.PASS


def test_market_session_warns_when_closed() -> None:
    result = check_market_session(_candidate(), _settings(), now=_MONDAY_AFTER_CLOSE)
    assert result.status == RuleStatus.WARNING


# --- Phase 5 extension: rule generalization for the F&O/IPO candidate scanners ---
# ema200 is deliberately absent from these snapshots — a recent IPO usually
# doesn't have 200 days of history, and the candidate scanners don't
# require it (see app.decision.rules.CANDIDATE_REQUIRED_FEATURE_KEYS).
_CANDIDATE_SNAPSHOT: dict[str, object] = {
    "price": "310",
    "adx14": "28",
    "relative_volume": "2.2",
    "resistance_level": "300",
}


def _candidate_scanner_candidate(
    *, scanner_name: str, score: float = 74.0, **snapshot_overrides: object
) -> DecisionCandidate:
    snapshot = {**_CANDIDATE_SNAPSHOT, **snapshot_overrides}
    return DecisionCandidate(
        symbol="NEWCO",
        scanner_name=scanner_name,
        signal_type="FNO_MOMENTUM",
        score=score,
        scan_date=date(2026, 1, 5),
        feature_snapshot=snapshot,
    )


def test_required_features_does_not_require_ema200_for_candidate_scanners() -> None:
    result = check_required_features(
        _candidate_scanner_candidate(scanner_name="fno_momentum_v1"), _settings()
    )
    assert result.status == RuleStatus.PASS


def test_required_features_still_requires_ema200_for_breakout_v1() -> None:
    result = check_required_features(_candidate(ema200=None), _settings())
    assert result.status == RuleStatus.FAIL


def test_trend_is_not_applicable_for_candidate_scanners() -> None:
    result = check_trend(_candidate_scanner_candidate(scanner_name="fno_momentum_v1"), _settings())
    assert result.status == RuleStatus.PASS
    assert "not applicable" in result.reason


def test_resistance_proximity_not_applicable_for_momentum_scanner() -> None:
    # price is far past resistance — would fail the proximity check if it
    # applied, but fno_momentum_v1 candidates are past resistance by design.
    result = check_resistance_proximity(
        _candidate_scanner_candidate(
            scanner_name="fno_momentum_v1", price="400", resistance_level="300"
        ),
        _settings(),
    )
    assert result.status == RuleStatus.PASS
    assert "not applicable" in result.reason


def test_resistance_proximity_still_applies_for_pre_breakout_scanner() -> None:
    settings = Settings(decision_resistance_distance_percent=3.0)
    result = check_resistance_proximity(
        _candidate_scanner_candidate(
            scanner_name="pre_breakout_v1", price="290", resistance_level="300"
        ),
        settings,
    )
    assert result.status == RuleStatus.FAIL  # ~3.3% away, just outside a 3% band
