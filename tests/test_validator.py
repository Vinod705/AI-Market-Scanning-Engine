"""Tests for app.data.validator."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.data.validator import DataValidator, ValidationError
from app.providers.base_provider import Candle, ProviderSymbol, Quote


def _candle(**overrides: object) -> Candle:
    defaults = {
        "timestamp": datetime(2026, 1, 5, 10, 0),
        "open": Decimal("100"),
        "high": Decimal("105"),
        "low": Decimal("99"),
        "close": Decimal("102"),
        "volume": 1000,
    }
    defaults.update(overrides)
    return Candle(**defaults)


def test_valid_candle_passes() -> None:
    DataValidator.validate_candle(_candle())


def test_negative_volume_rejected() -> None:
    with pytest.raises(ValidationError, match="volume"):
        DataValidator.validate_candle(_candle(volume=-1))


def test_high_below_low_rejected() -> None:
    with pytest.raises(ValidationError, match="high"):
        DataValidator.validate_candle(_candle(high=Decimal("90"), low=Decimal("99")))


def test_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        DataValidator.validate_candle(_candle(open=Decimal("-10")))


def test_nan_price_rejected() -> None:
    # Pydantic already rejects NaN at Candle construction time (finite_number
    # constraint), so use model_construct to bypass it and exercise
    # DataValidator's own corrupted-data guard directly — this is the
    # defense-in-depth path for data that reaches the validator through
    # some other route than Candle(...).
    candle = _candle()
    corrupted = candle.model_construct(**{**candle.model_dump(), "open": Decimal("NaN")})
    with pytest.raises(ValidationError):
        DataValidator.validate_candle(corrupted)


def test_validate_symbol_rejects_empty_fields() -> None:
    with pytest.raises(ValidationError):
        DataValidator.validate_symbol(ProviderSymbol(symbol="", exchange="N", instrument_token="1"))
    with pytest.raises(ValidationError):
        DataValidator.validate_symbol(
            ProviderSymbol(symbol="TCS", exchange="", instrument_token="1")
        )


def test_validate_symbol_accepts_valid() -> None:
    DataValidator.validate_symbol(
        ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536")
    )


def test_validate_quote() -> None:
    quote = Quote(
        symbol="TCS",
        ltp=Decimal("3500"),
        open=Decimal("3480"),
        high=Decimal("3520"),
        low=Decimal("3470"),
        close=Decimal("3490"),
        volume=500,
        timestamp=datetime.now(),
    )
    DataValidator.validate_quote(quote)


def test_validate_candles_drops_duplicate_timestamps() -> None:
    ts = datetime(2026, 1, 5, 10, 0)
    candles = [
        _candle(timestamp=ts),
        _candle(timestamp=ts),
        _candle(timestamp=ts + timedelta(minutes=1)),
    ]

    clean = DataValidator.validate_candles(candles, context="TCS")

    assert len(clean) == 2
    assert clean[0].timestamp == ts


def test_validate_candles_drops_invalid_and_keeps_valid() -> None:
    candles = [_candle(), _candle(timestamp=datetime(2026, 1, 5, 10, 1), open=Decimal("-5"))]

    clean = DataValidator.validate_candles(candles, context="TCS")

    assert len(clean) == 1
