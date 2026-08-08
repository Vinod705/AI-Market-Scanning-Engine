"""Scanner domain types.

These are plain dataclasses the scanner package operates on internally —
not SQLAlchemy models (those live in `app/models/`, alongside every other
ORM model in the project) and not API schemas (those live in
`app/schemas/`). Keeping the scanner's own vocabulary separate from both
lets `BaseScanner` subclasses stay free of persistence and HTTP concerns.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol


@runtime_checkable
class ScannerContext(Protocol):
    """Structural contract every scan context satisfies.

    `ScanContext` below (breakout_v1) and `app.candidates.models.CandidateContext`
    (the F&O/IPO candidate scanners) both implement this without sharing a
    base class — that's what lets `BaseScanner`/`ScannerManager` stay
    agnostic to which shape a given scanner actually uses.
    """

    @property
    def symbol(self) -> Symbol: ...

    @property
    def scan_date(self) -> date: ...

    def feature_snapshot(self) -> dict[str, object]: ...


@dataclass
class ScanContext:
    """Everything a scanner needs to evaluate one symbol on one trading day."""

    symbol: Symbol
    features: DailyFeature
    price: Decimal | None  # latest close, read from daily_prices (not stored on DailyFeature)

    @property
    def scan_date(self) -> date:
        return self.features.date

    def feature_snapshot(self) -> dict[str, object]:
        """A JSON-serializable snapshot of the inputs a scan decision was based on —
        stored alongside the result so a rejected/qualified call can be audited
        later without re-deriving it from a feature row that may have since changed."""
        snapshot: dict[str, object] = {"price": str(self.price) if self.price is not None else None}
        for column in self.features.__table__.columns:
            if column.name in {"id", "symbol_id", "created_at", "updated_at"}:
                continue
            value = getattr(self.features, column.name)
            if isinstance(value, Decimal):
                value = str(value)
            elif isinstance(value, datetime | date):
                value = value.isoformat()
            snapshot[column.name] = value
        return snapshot


@dataclass
class ValidationResult:
    valid: bool
    reason: str | None = None


@dataclass
class ScanOutcome:
    qualified: bool
    reason: str
