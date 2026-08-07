"""Generic validation helpers scanners compose in their `validate()` implementation.

Duplicate-result rejection isn't handled here — `ScannerManager` skips a
symbol before doing any scan work at all if a result already exists for
that (symbol, scanner, date), which is cheaper than scanning and then
discarding. See `app/scanner/scanner_manager.py`.
"""

from app.scanner.models import ScanContext, ValidationResult


class ScannerValidator:
    """Stateless checks over a `ScanContext`. Scanner-specific field/range
    requirements are passed in by the caller rather than hardcoded here, so
    this stays reusable across scanners with different data needs."""

    @staticmethod
    def require_fields(context: ScanContext, field_names: list[str]) -> ValidationResult:
        """Reject if any named `DailyFeature` attribute (or `"price"`, which
        lives on the context itself) is None — covers both "missing market
        features" and "insufficient historical data" (e.g. `ema200` is None
        until 200 bars of history exist)."""
        for field_name in field_names:
            value = (
                context.price
                if field_name == "price"
                else getattr(context.features, field_name, None)
            )
            if value is None:
                return ValidationResult(valid=False, reason=f"missing required field: {field_name}")
        return ValidationResult(valid=True)

    @staticmethod
    def require_ranges(
        context: ScanContext, ranges: dict[str, tuple[float, float]]
    ) -> ValidationResult:
        """Reject if a named field is outside [low, high] — catches corrupted/
        out-of-domain feature values (e.g. an RSI or ADX outside 0-100)."""
        for field_name, (low, high) in ranges.items():
            value = getattr(context.features, field_name, None)
            if value is None:
                continue  # require_fields is responsible for missing-value rejection
            numeric_value = float(value)
            if not (low <= numeric_value <= high):
                return ValidationResult(
                    valid=False,
                    reason=f"{field_name}={numeric_value} outside expected range [{low}, {high}]",
                )
        return ValidationResult(valid=True)
