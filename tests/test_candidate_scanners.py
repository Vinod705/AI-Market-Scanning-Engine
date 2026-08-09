"""Unit tests for the F&O/IPO candidate scanners' validate/scan/score logic.

Builds `CandidateContext` directly from a hand-constructed `StockCandidate`
rather than going through `app.candidates.builder.build_candidate` (which
needs a database) — these scanners' qualification logic is a pure function
of an already-scored candidate, so it's tested that way.
"""

from decimal import Decimal

from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.ipo_intraday_scanner import IpoIntradayScanner
from app.candidates.models import (
    AlertCategory,
    CandidateContext,
    SetupState,
    StockCandidate,
    Universe,
)
from app.candidates.pre_breakout_scanner import PreBreakoutScanner
from app.config.settings import Settings
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.models.symbol import Symbol


def _symbol() -> Symbol:
    return Symbol(id=1, symbol="NEWCO", exchange="N", instrument_token="1")


def _candidate(
    *,
    universe: Universe,
    scanner_type: str,
    setup_state: SetupState | None,
    overall_score: float = 70.0,
    relative_volume: str | None = "2.5",
    adx14: str | None = "28",
) -> StockCandidate:
    return StockCandidate(
        symbol="NEWCO",
        instrument_type="EQUITY",
        universe=universe,
        scanner_type=scanner_type,
        price=Decimal("310"),
        breakout_level=Decimal("300"),
        support_level=Decimal("280"),
        resistance_level=Decimal("300"),
        fundamental_score=None,
        technical_score=overall_score,
        overall_score=overall_score,
        quality="HIGH",
        fundamental_reasons=[],
        technical_reasons=["Trend direction favorable"],
        risk_flags=["Fundamental Score: UNKNOWN (no data source)"],
        passed_rules=[],
        failed_rules=[],
        data_completeness_pct=0.0,
        technical_data_completeness_pct=100.0,
        setup_state=setup_state,
        alert_category=None,
        reason="",
        technical_feature_snapshot={"relative_volume": relative_volume, "adx14": adx14},
    )


def _fno_momentum_scanner() -> FnoMomentumScanner:
    return FnoMomentumScanner(Settings(), UnavailableFundamentalDataProvider())


def _pre_breakout_scanner() -> PreBreakoutScanner:
    return PreBreakoutScanner(Settings(), UnavailableFundamentalDataProvider())


def _ipo_intraday_scanner() -> IpoIntradayScanner:
    return IpoIntradayScanner(Settings(), UnavailableFundamentalDataProvider())


# --- FnoMomentumScanner ---


def test_fno_momentum_qualifies_strong_momentum_setup() -> None:
    scanner = _fno_momentum_scanner()
    candidate = _candidate(
        universe=Universe.FNO,
        scanner_type="fno_momentum_v1",
        setup_state=SetupState.MOMENTUM,
        overall_score=70.0,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.FNO_MOMENTUM
    assert scanner.score(context) == 70.0


def test_fno_momentum_assigns_breakout_category_for_confirmed_state() -> None:
    scanner = _fno_momentum_scanner()
    candidate = _candidate(
        universe=Universe.FNO,
        scanner_type="fno_momentum_v1",
        setup_state=SetupState.BREAKOUT_CONFIRMED,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.FNO_BREAKOUT


def test_fno_momentum_rejects_pre_breakout_setup() -> None:
    scanner = _fno_momentum_scanner()
    candidate = _candidate(
        universe=Universe.FNO, scanner_type="fno_momentum_v1", setup_state=SetupState.PRE_BREAKOUT
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert candidate.alert_category is None


def test_fno_momentum_rejects_when_score_below_threshold() -> None:
    settings = Settings(fno_momentum_min_score=80.0)
    scanner = FnoMomentumScanner(settings, UnavailableFundamentalDataProvider())
    candidate = _candidate(
        universe=Universe.FNO,
        scanner_type="fno_momentum_v1",
        setup_state=SetupState.MOMENTUM,
        overall_score=60.0,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "overall_score>=threshold" in candidate.failed_rules


# --- PreBreakoutScanner ---


def test_pre_breakout_qualifies_and_labels_fno_category() -> None:
    scanner = _pre_breakout_scanner()
    candidate = _candidate(
        universe=Universe.FNO, scanner_type="pre_breakout_v1", setup_state=SetupState.PRE_BREAKOUT
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.FNO_PRE_BREAKOUT


def test_pre_breakout_labels_ipo_category_for_ipo_universe() -> None:
    scanner = _pre_breakout_scanner()
    candidate = _candidate(
        universe=Universe.IPO, scanner_type="pre_breakout_v1", setup_state=SetupState.PRE_BREAKOUT
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.IPO_PRE_BREAKOUT


def test_pre_breakout_rejects_momentum_setup() -> None:
    scanner = _pre_breakout_scanner()
    candidate = _candidate(
        universe=Universe.FNO, scanner_type="pre_breakout_v1", setup_state=SetupState.MOMENTUM
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False


# --- IpoIntradayScanner ---


def test_ipo_intraday_qualifies_momentum_with_volume_confirmation() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _candidate(
        universe=Universe.IPO,
        scanner_type="ipo_intraday_v1",
        setup_state=SetupState.MOMENTUM,
        relative_volume="3.0",
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.IPO_MOMENTUM


def test_ipo_intraday_rejects_without_volume_confirmation() -> None:
    settings = Settings(ipo_intraday_min_rvol=2.0)
    scanner = IpoIntradayScanner(settings, UnavailableFundamentalDataProvider())
    candidate = _candidate(
        universe=Universe.IPO,
        scanner_type="ipo_intraday_v1",
        setup_state=SetupState.MOMENTUM,
        relative_volume="1.2",
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "relative_volume>=threshold" in candidate.failed_rules


def test_validate_fails_when_setup_state_is_none() -> None:
    scanner = _fno_momentum_scanner()
    candidate = _candidate(universe=Universe.FNO, scanner_type="fno_momentum_v1", setup_state=None)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    result = scanner.validate(context)

    assert result.valid is False


def test_validate_fails_when_required_raw_features_missing() -> None:
    scanner = _fno_momentum_scanner()
    candidate = _candidate(
        universe=Universe.FNO,
        scanner_type="fno_momentum_v1",
        setup_state=SetupState.MOMENTUM,
        relative_volume=None,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    result = scanner.validate(context)

    assert result.valid is False
