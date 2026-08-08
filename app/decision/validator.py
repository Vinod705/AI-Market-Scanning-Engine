"""Generic validation helpers the decision rules compose.

Mirrors `app/scanner/validator.py`'s role for `BreakoutScanner`: small,
stateless, reusable checks that individual rules call into rather than
each reimplementing feature-presence/staleness logic themselves.
"""

from datetime import date as date_

_MISSING_SENTINELS = (None, "None")


class DecisionValidator:
    @staticmethod
    def missing_features(feature_snapshot: dict[str, object], keys: list[str]) -> list[str]:
        """Names of `keys` that are absent or null in `feature_snapshot`."""
        return [key for key in keys if feature_snapshot.get(key) in _MISSING_SENTINELS]

    @staticmethod
    def is_fresh(scan_date: date_, *, max_age_days: int, today: date_ | None = None) -> bool:
        """Whether `scan_date` is within `max_age_days` of `today` (defaults
        to the real current date)."""
        reference = today or date_.today()
        return (reference - scan_date).days <= max_age_days

    @staticmethod
    def as_float(feature_snapshot: dict[str, object], key: str) -> float | None:
        """Feature snapshots are JSON (values stringified from Decimal), so
        every numeric read goes through this rather than a bare cast."""
        value = feature_snapshot.get(key)
        if value in _MISSING_SENTINELS:
            return None
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
