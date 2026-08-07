"""Tests for app.scanner.breakout_scanner.BreakoutScanner."""

from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.models import ScanContext

_QUALIFYING_FEATURES: dict[str, object] = {
    "ema20": Decimal("105"),
    "ema50": Decimal("100"),
    "ema200": Decimal("90"),
    "adx14": Decimal("25"),
    "relative_volume": Decimal("2.0"),
    "resistance_level": Decimal("112"),
    "trend_strength": Decimal("80"),
    "momentum_score": Decimal("50"),
    "atr_expansion": True,
    "rs_vs_nifty": Decimal("10"),
}


def _context(price: Decimal = Decimal("110"), **overrides: object) -> ScanContext:
    symbol = Symbol(id=1, symbol="TCS", exchange="N", instrument_token="1")
    values = {**_QUALIFYING_FEATURES, **overrides}
    features = DailyFeature(symbol_id=1, **values)
    return ScanContext(symbol=symbol, features=features, price=price)


@pytest.fixture
def scanner() -> BreakoutScanner:
    return BreakoutScanner(Settings())


def test_validate_passes_with_full_feature_set(scanner: BreakoutScanner) -> None:
    result = scanner.validate(_context())
    assert result.valid is True


def test_validate_rejects_missing_ema200(scanner: BreakoutScanner) -> None:
    result = scanner.validate(_context(ema200=None))
    assert result.valid is False
    assert result.reason is not None
    assert "ema200" in result.reason


def test_validate_rejects_adx_out_of_range(scanner: BreakoutScanner) -> None:
    result = scanner.validate(_context(adx14=Decimal("150")))
    assert result.valid is False


def test_scan_qualifies_when_all_conditions_met(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context())
    assert outcome.qualified is True
    assert "all conditions met" in outcome.reason


def test_scan_rejects_when_price_below_ema20(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context(price=Decimal("100")))
    assert outcome.qualified is False
    assert "price>EMA20" in outcome.reason


def test_scan_rejects_when_ema_stack_not_aligned(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context(ema50=Decimal("106")))  # ema20 < ema50, breaks the stack
    assert outcome.qualified is False
    assert "EMA20>EMA50" in outcome.reason


def test_scan_rejects_when_adx_below_threshold(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context(adx14=Decimal("10")))
    assert outcome.qualified is False
    assert "ADX>threshold" in outcome.reason


def test_scan_rejects_when_relative_volume_below_threshold(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context(relative_volume=Decimal("0.5")))
    assert outcome.qualified is False
    assert "relative_volume>threshold" in outcome.reason
    assert "volume_increasing" in outcome.reason


def test_scan_rejects_when_not_near_resistance(scanner: BreakoutScanner) -> None:
    outcome = scanner.scan(_context(resistance_level=Decimal("200")))
    assert outcome.qualified is False
    assert "near_resistance" in outcome.reason


def test_score_matches_expected_weighted_composite(scanner: BreakoutScanner) -> None:
    score = scanner.score(_context())
    assert score == pytest.approx(77.07, abs=0.01)


def test_score_is_always_within_bounds(scanner: BreakoutScanner) -> None:
    # A deliberately unfavorable context should still clamp into [0, 100].
    score = scanner.score(
        _context(
            trend_strength=None,
            momentum_score=None,
            relative_volume=Decimal("0"),
            atr_expansion=False,
            atr_contraction=False,
            rs_vs_nifty=None,
            resistance_level=None,
        )
    )
    assert 0.0 <= score <= 100.0
