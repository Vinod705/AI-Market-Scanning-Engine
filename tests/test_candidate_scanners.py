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
    price: Decimal | None = Decimal("310"),
    ipo_52w_high: str | None = None,
    ipo_52w_low: str | None = None,
    ipo_latest_volume: int | None = None,
) -> StockCandidate:
    snapshot: dict[str, object] = {"relative_volume": relative_volume, "adx14": adx14}
    if ipo_52w_high is not None:
        snapshot["ipo_52w_high"] = ipo_52w_high
    if ipo_52w_low is not None:
        snapshot["ipo_52w_low"] = ipo_52w_low
    if ipo_latest_volume is not None:
        snapshot["ipo_latest_volume"] = ipo_latest_volume

    return StockCandidate(
        symbol="NEWCO",
        instrument_type="EQUITY",
        universe=universe,
        scanner_type=scanner_type,
        price=price,
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
        technical_feature_snapshot=snapshot,
    )


def _fno_momentum_scanner() -> FnoMomentumScanner:
    return FnoMomentumScanner(Settings())


def _pre_breakout_scanner() -> PreBreakoutScanner:
    return PreBreakoutScanner(Settings())


def _ipo_intraday_scanner() -> IpoIntradayScanner:
    return IpoIntradayScanner(Settings())


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
    scanner = FnoMomentumScanner(settings)
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
        price=Decimal("310"),
        ipo_52w_high="320",
        ipo_52w_low="140",
        ipo_latest_volume=300_000,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True
    assert candidate.alert_category == AlertCategory.IPO_MOMENTUM


# --- IpoIntradayScanner: 52-week-high/52-week-low/price/volume filters
# (see PriceRepository.get_52_week_high_low) ---


def _ipo_candidate(
    *,
    setup_state: SetupState | None = SetupState.MOMENTUM,
    price: Decimal | None = Decimal("100"),
    ipo_52w_high: str | None = "105",
    ipo_52w_low: str | None = "45",
    ipo_latest_volume: int | None = 300_000,
) -> StockCandidate:
    """A candidate that passes every ipo_intraday_v1 check by default —
    each boundary test overrides exactly one field so a failure is
    attributable to the check under test."""
    return _candidate(
        universe=Universe.IPO,
        scanner_type="ipo_intraday_v1",
        setup_state=setup_state,
        relative_volume="3.0",
        price=price,
        ipo_52w_high=ipo_52w_high,
        ipo_52w_low=ipo_52w_low,
        ipo_latest_volume=ipo_latest_volume,
    )


def test_ipo_intraday_rejects_price_at_52w_high_exactly() -> None:
    """0 < pct_below < threshold is strict on both ends — price AT the
    high (0% below) must not qualify, only a genuine pullback does."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("105"), ipo_52w_high="105")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "price_within_pct_below_52w_high" in candidate.failed_rules


def test_ipo_intraday_rejects_exactly_at_max_pct_below_52w_high() -> None:
    """price=90, 52w_high=100 -> exactly 10% below -> must fail (< threshold, not <=)."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("90"), ipo_52w_high="100")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "price_within_pct_below_52w_high" in candidate.failed_rules


def test_ipo_intraday_qualifies_just_under_max_pct_below_52w_high() -> None:
    """price=90, 52w_high=99 -> ~9.09% below -> passes. 52w_low lowered
    to 40 so price/52w_low (125% above) clears the separate low-distance
    check too — otherwise this would fail on an unrelated check."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("90"), ipo_52w_high="99", ipo_52w_low="40")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True


def test_ipo_intraday_rejects_exactly_double_52w_low() -> None:
    """price=100, 52w_low=50 -> exactly 100% above -> must fail (> threshold, not >=)."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("100"), ipo_52w_low="50")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "price_above_52w_low_multiple" in candidate.failed_rules


def test_ipo_intraday_qualifies_just_above_double_52w_low() -> None:
    """price=100, 52w_low=49 -> ~104% above -> passes."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("100"), ipo_52w_low="49")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True


def test_ipo_intraday_rejects_price_at_min_price_exactly() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("25"), ipo_52w_high="26", ipo_52w_low="10")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "price>min_price" in candidate.failed_rules


def test_ipo_intraday_qualifies_just_above_min_price() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(price=Decimal("25.01"), ipo_52w_high="26", ipo_52w_low="10")
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True


def test_ipo_intraday_rejects_volume_at_min_volume_exactly() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(ipo_latest_volume=250_000)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "volume>min_volume" in candidate.failed_rules


def test_ipo_intraday_qualifies_just_above_min_volume() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(ipo_latest_volume=250_001)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is True


def test_ipo_intraday_reports_insufficient_data_when_52w_high_low_missing() -> None:
    """A brand-new IPO with no 52-week high/low/volume yet (see
    IpoIntradayScanner.build_context) must fail these checks cleanly —
    never crash, never fabricate a value."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(ipo_52w_high=None, ipo_52w_low=None, ipo_latest_volume=None)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "price_within_pct_below_52w_high" in candidate.failed_rules
    assert "price_above_52w_low_multiple" in candidate.failed_rules
    assert "volume>min_volume" in candidate.failed_rules


def test_ipo_intraday_still_respects_existing_setup_state_rule() -> None:
    """The new price/volume filters are additive — an otherwise-perfect
    price/volume profile still fails without a qualifying setup_state."""
    scanner = _ipo_intraday_scanner()
    candidate = _ipo_candidate(setup_state=SetupState.PRE_BREAKOUT)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "setup_state_is_momentum_or_confirmed" in candidate.failed_rules


def test_ipo_intraday_rejects_without_volume_confirmation() -> None:
    settings = Settings(ipo_intraday_min_rvol=2.0)
    scanner = IpoIntradayScanner(settings)
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


# --- validate() relaxation for the IPO universe (insufficient technical
# history — see app.universe.provider.UniverseProvider.get_ipo_universe) ---


def test_validate_passes_for_ipo_candidate_with_no_setup_state_or_features() -> None:
    """A newly-listed IPO (e.g. 3 trading days old) legitimately has no
    setup_state/adx14/relative_volume yet — those need rolling windows
    this young a stock hasn't filled. It must still get a persisted
    scanner_results row (via scan()) instead of vanishing at validate()."""
    scanner = _ipo_intraday_scanner()
    candidate = _candidate(
        universe=Universe.IPO,
        scanner_type="ipo_intraday_v1",
        setup_state=None,
        relative_volume=None,
        adx14=None,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    result = scanner.validate(context)

    assert result.valid is True


def test_scan_reports_insufficient_history_instead_of_crashing_for_new_ipo() -> None:
    scanner = _ipo_intraday_scanner()
    candidate = _candidate(
        universe=Universe.IPO,
        scanner_type="ipo_intraday_v1",
        setup_state=None,
        relative_volume=None,
        adx14=None,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    outcome = scanner.scan(context)

    assert outcome.qualified is False
    assert "setup_state_is_momentum_or_confirmed" in candidate.failed_rules
    assert "relative_volume>=threshold" in candidate.failed_rules


def test_validate_passes_for_pre_breakout_ipo_candidate_with_no_setup_state() -> None:
    """pre_breakout_v1 also evaluates the IPO universe (see
    PreBreakoutScanner.get_candidate_symbols) — same relaxation applies."""
    scanner = _pre_breakout_scanner()
    candidate = _candidate(
        universe=Universe.IPO,
        scanner_type="pre_breakout_v1",
        setup_state=None,
        relative_volume=None,
        adx14=None,
    )
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    result = scanner.validate(context)

    assert result.valid is True


def test_validate_still_fails_for_fno_candidate_via_pre_breakout_scanner() -> None:
    """Regression guard: the IPO relaxation must not leak into F&O
    candidates evaluated by the same shared scanner."""
    scanner = _pre_breakout_scanner()
    candidate = _candidate(universe=Universe.FNO, scanner_type="pre_breakout_v1", setup_state=None)
    context = CandidateContext(symbol=_symbol(), candidate=candidate)

    result = scanner.validate(context)

    assert result.valid is False
