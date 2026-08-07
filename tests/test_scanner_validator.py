"""Tests for app.scanner.validator.ScannerValidator."""

from decimal import Decimal

from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol
from app.scanner.models import ScanContext
from app.scanner.validator import ScannerValidator


def _context(*, price: Decimal | None = Decimal("100"), **feature_overrides: object) -> ScanContext:
    symbol = Symbol(id=1, symbol="TCS", exchange="N", instrument_token="1")
    features = DailyFeature(symbol_id=1, **feature_overrides)
    return ScanContext(symbol=symbol, features=features, price=price)


def test_require_fields_passes_when_all_present() -> None:
    context = _context(ema20=Decimal("10"), adx14=Decimal("25"))
    result = ScannerValidator.require_fields(context, ["price", "ema20", "adx14"])
    assert result.valid is True
    assert result.reason is None


def test_require_fields_rejects_when_feature_is_none() -> None:
    context = _context(ema20=None)
    result = ScannerValidator.require_fields(context, ["ema20"])
    assert result.valid is False
    assert result.reason is not None
    assert "ema20" in result.reason


def test_require_fields_rejects_when_price_is_none() -> None:
    context = _context(price=None)
    result = ScannerValidator.require_fields(context, ["price"])
    assert result.valid is False
    assert result.reason is not None
    assert "price" in result.reason


def test_require_ranges_passes_when_within_bounds() -> None:
    context = _context(adx14=Decimal("50"))
    result = ScannerValidator.require_ranges(context, {"adx14": (0.0, 100.0)})
    assert result.valid is True


def test_require_ranges_rejects_when_out_of_bounds() -> None:
    context = _context(adx14=Decimal("150"))
    result = ScannerValidator.require_ranges(context, {"adx14": (0.0, 100.0)})
    assert result.valid is False
    assert result.reason is not None
    assert "adx14" in result.reason


def test_require_ranges_skips_missing_field() -> None:
    """require_ranges defers missing-value rejection to require_fields."""
    context = _context(adx14=None)
    result = ScannerValidator.require_ranges(context, {"adx14": (0.0, 100.0)})
    assert result.valid is True
