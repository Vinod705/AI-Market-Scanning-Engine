"""StockCandidate: the unified output shape for IPO/F&O scanners.

Plain dataclasses/enums, not ORM models (those live in `app/models/`) and
not API schemas (those live in `app/schemas/`) — same split
`app/scanner/models.py` and `app/decision/models.py` use.

Persistence deliberately reuses the existing `scanner_results` table
(unique on symbol/scanner/date, exactly like breakout_v1) rather than a
new schema: `StockCandidate.to_feature_snapshot()` packs every field this
module adds into the same JSON `feature_snapshot` column, and
`ScannerResult.score` holds `overall_score`. No migration needed for the
new scanners themselves — see `app/models/fno_universe.py` for the one
genuinely new table (F&O universe membership, which isn't a per-scan
result at all).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.models.symbol import Symbol


class Universe(StrEnum):
    IPO = "IPO"
    FNO = "FNO"
    LISTED = "LISTED"


class SetupState(StrEnum):
    PRE_BREAKOUT = "PRE_BREAKOUT"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    MOMENTUM = "MOMENTUM"


class AlertCategory(StrEnum):
    IPO_PRE_BREAKOUT = "IPO_PRE_BREAKOUT"
    IPO_BREAKOUT = "IPO_BREAKOUT"
    IPO_MOMENTUM = "IPO_MOMENTUM"
    FNO_PRE_BREAKOUT = "FNO_PRE_BREAKOUT"
    FNO_BREAKOUT = "FNO_BREAKOUT"
    FNO_MOMENTUM = "FNO_MOMENTUM"


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


@dataclass
class StockCandidate:
    """One scanner's unified verdict for one symbol at one point in time."""

    symbol: str
    instrument_type: (
        str  # "EQUITY" — universe (IPO/F&O/LISTED) is a classification, not a contract type
    )
    universe: Universe
    scanner_type: str  # scanner_name, e.g. "fno_momentum_v1"
    price: Decimal | None
    breakout_level: Decimal | None
    support_level: Decimal | None
    resistance_level: Decimal | None
    fundamental_score: float | None  # None only when tier=UNKNOWN (zero usable factors)
    technical_score: float
    overall_score: float
    quality: str  # HIGH | MEDIUM | LOW
    fundamental_reasons: list[str]  # positive factors only — feeds the alert's "Why" bullets
    technical_reasons: list[str]  # positive factors only — feeds the alert's "Why" bullets
    risk_flags: list[str]
    passed_rules: list[str]
    failed_rules: list[str]
    data_completeness_pct: float  # fundamental data completeness
    technical_data_completeness_pct: float
    setup_state: SetupState | None
    alert_category: AlertCategory | None
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)
    # Which discovery source(s) independently flagged this symbol — see
    # `app.candidates.sources`. Defaults to 5paisa-only: every candidate
    # this pipeline produces is already 5paisa-data-driven by construction.
    scanner_sources: list[str] = field(default_factory=lambda: ["5PAISA"])
    # Full per-factor breakdown (factor_name/category/value/normalized_score/
    # weight/contribution/status/reason each) — favorable, unfavorable, AND
    # unknown factors alike, for audit via the API. See
    # `app.fundamentals.models.FundamentalFactor` / `app.technical.models.TechnicalFactor`.
    fundamental_factors: list[dict[str, object]] = field(default_factory=list)
    technical_factors: list[dict[str, object]] = field(default_factory=list)
    # Raw technical feature values (ema20, adx14, relative_volume, ...) —
    # merged into the persisted snapshot alongside everything above.
    technical_feature_snapshot: dict[str, object] = field(default_factory=dict)

    def to_feature_snapshot(self) -> dict[str, object]:
        """JSON-serializable snapshot persisted to `scanner_results.feature_snapshot`
        — everything the Decision Engine and Telegram formatter need, without
        a schema change."""
        snapshot: dict[str, object] = dict(self.technical_feature_snapshot)
        snapshot.update(
            {
                "price": _decimal_str(self.price),
                "breakout_level": _decimal_str(self.breakout_level),
                "support_level": _decimal_str(self.support_level),
                "resistance_level": _decimal_str(self.resistance_level),
                "instrument_type": self.instrument_type,
                "universe": self.universe.value,
                "scanner_type": self.scanner_type,
                "fundamental_score": self.fundamental_score,
                "technical_score": self.technical_score,
                "overall_score": self.overall_score,
                "quality": self.quality,
                "scanner_sources": list(self.scanner_sources),
                "fundamental_reasons": list(self.fundamental_reasons),
                "technical_reasons": list(self.technical_reasons),
                "fundamental_factors": list(self.fundamental_factors),
                "technical_factors": list(self.technical_factors),
                "risk_flags": list(self.risk_flags),
                "data_completeness_pct": self.data_completeness_pct,
                "technical_data_completeness_pct": self.technical_data_completeness_pct,
                "setup_state": self.setup_state.value if self.setup_state else None,
                "alert_category": self.alert_category.value if self.alert_category else None,
                # Set by the scanner's scan() before save_results() persists this
                # snapshot — the scanner's own qualification checks, distinct from
                # (and in addition to) the Decision Engine's independent re-validation.
                "passed_rules": list(self.passed_rules),
                "failed_rules": list(self.failed_rules),
                "reason": self.reason,
            }
        )
        return snapshot


@dataclass
class CandidateContext:
    """Adapter so `BaseScanner.save_results` (which only needs `.symbol.id`
    and `.feature_snapshot()`) works unchanged for the new candidate-based
    scanners — reused as-is, not touched, not duplicated. Separate from
    `app.scanner.models.ScanContext`, which stays purely DailyFeature-shaped
    for `breakout_v1`."""

    symbol: Symbol
    candidate: StockCandidate

    @property
    def scan_date(self) -> date:
        return self.candidate.timestamp.date()

    def feature_snapshot(self) -> dict[str, object]:
        return self.candidate.to_feature_snapshot()
