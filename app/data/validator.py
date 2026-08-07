"""Market data validation.

Runs on every candle/quote/symbol before it reaches the database. Nothing
here talks to the database or a provider — pure checks, so they're cheap
to unit test.
"""

import math
from datetime import datetime
from decimal import Decimal

from loguru import logger

from app.providers.base_provider import Candle, ProviderSymbol, Quote


class ValidationError(Exception):
    """Raised when a single record fails validation."""


class DataValidator:
    """Stateless validation rules for provider data."""

    @staticmethod
    def validate_symbol(symbol: ProviderSymbol) -> None:
        if not symbol.symbol or not symbol.symbol.strip():
            raise ValidationError("Symbol code is empty")
        if not symbol.exchange or not symbol.exchange.strip():
            raise ValidationError(f"Symbol {symbol.symbol} has no exchange")
        if not symbol.instrument_token or not symbol.instrument_token.strip():
            raise ValidationError(f"Symbol {symbol.symbol} has no instrument token")

    @staticmethod
    def validate_quote(quote: Quote) -> None:
        DataValidator._validate_ohlc(
            quote.open, quote.high, quote.low, quote.close, context=quote.symbol
        )
        DataValidator._validate_non_negative(quote.volume, name="volume", context=quote.symbol)

    @staticmethod
    def validate_candle(candle: Candle, *, context: str = "") -> None:
        DataValidator._validate_ohlc(
            candle.open, candle.high, candle.low, candle.close, context=context
        )
        DataValidator._validate_non_negative(candle.volume, name="volume", context=context)
        if candle.vwap is not None:
            DataValidator._validate_non_negative(candle.vwap, name="vwap", context=context)

    @staticmethod
    def validate_candles(candles: list[Candle], *, context: str = "") -> list[Candle]:
        """Validate a batch, dropping (and logging) any invalid or duplicate-timestamp candles."""
        clean: list[Candle] = []
        seen_timestamps: set[datetime] = set()

        for candle in candles:
            if candle.timestamp in seen_timestamps:
                logger.warning(
                    "Dropping duplicate-timestamp candle for {context} at {ts}",
                    context=context,
                    ts=candle.timestamp,
                )
                continue

            try:
                DataValidator.validate_candle(candle, context=context)
            except ValidationError as exc:
                logger.warning(
                    "Dropping invalid candle for {context}: {error}", context=context, error=exc
                )
                continue

            seen_timestamps.add(candle.timestamp)
            clean.append(candle)

        return clean

    # --- internals -----------------------------------------------------

    @staticmethod
    def _validate_non_negative(value: Decimal | int | float, *, name: str, context: str) -> None:
        if DataValidator._is_corrupted(value):
            raise ValidationError(f"{context}: {name} is missing or corrupted ({value!r})")
        if value < 0:
            raise ValidationError(f"{context}: {name} is negative ({value})")

    @staticmethod
    def _validate_ohlc(
        open_: Decimal, high: Decimal, low: Decimal, close: Decimal, *, context: str
    ) -> None:
        for name, value in (("open", open_), ("high", high), ("low", low), ("close", close)):
            if value is None or DataValidator._is_corrupted(value):
                raise ValidationError(f"{context}: missing/corrupted {name}")
            if value < 0:
                raise ValidationError(f"{context}: negative {name} ({value})")

        if high < low:
            raise ValidationError(f"{context}: high ({high}) < low ({low})")
        if high < open_ or high < close:
            raise ValidationError(f"{context}: high ({high}) is less than open/close")
        if low > open_ or low > close:
            raise ValidationError(f"{context}: low ({low}) is greater than open/close")

    @staticmethod
    def _is_corrupted(value: Decimal | int | float) -> bool:
        try:
            return math.isnan(float(value)) or math.isinf(float(value))
        except (TypeError, ValueError):
            return True
