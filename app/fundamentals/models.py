"""Fundamental Intelligence Engine domain types.

Screener.in (screener.in) was evaluated as a reference for which metrics a
fundamental-data source for Indian equities should support — its public
company pages expose P/E, P/B, ROE, ROCE, growth trends, cash flow,
promoter/FII/DII holding, and machine-generated pros/cons, which is the
taxonomy `FundamentalData` below mirrors. It has no documented/authorized
API or developer program for third-party programmatic access (checked:
no API/developer link anywhere on the site or its navigation, no public
API docs — just an "Export to Excel" button for individual
premium-subscriber use, and a lightly-restrictive robots.txt that permits
crawling but is not authorization to build a commercial data pipeline
against it). Scraping it is out of scope per this project's constraints.
Screener.in is therefore used here as a *methodology reference only* —
see `app.fundamentals.unavailable_provider` for the actual (data-less)
provider currently wired in, and `app.fundamentals.provider` for the
abstraction a real, licensed data source would implement.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class FundamentalTier(StrEnum):
    GOOD = "GOOD"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"  # not enough data to judge at all


class FactorStatus(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


class FieldAvailability(StrEnum):
    """Per-field availability, distinct from FactorStatus (which judges a
    value as good/bad). Only AVAILABLE/UNKNOWN are ever set by a provider
    today — the IPO-specific states exist so a future provider that can
    actually tell "no data because this IPO has no history yet" apart from
    "this data point simply isn't reported" can say so honestly, without
    another model change."""

    AVAILABLE = "AVAILABLE"
    LIMITED_HISTORY = "LIMITED_HISTORY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class FieldSnapshot:
    """Provenance for one `FundamentalData` field — which source reported
    it, for what period, and its availability status. Purely additive:
    `FundamentalScorer` never reads this, only the plain float fields on
    `FundamentalData` — this exists solely so the dashboard/Telegram/API
    can show WHERE a number came from (see Phase 7 multi-source spec)."""

    field_name: str
    value: float | None
    source: str | None
    period: str | None
    status: FieldAvailability
    as_of: date | None = None
    alternates: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class FundamentalFactor:
    """One scored input into the Fundamental Score — stored individually
    (not just folded into a single number) so the score is always
    explainable, per the spec's explicit requirement."""

    factor_name: str
    category: str  # growth | profitability | financial_strength | cash_flow | valuation | ownership
    value: str | None
    normalized_score: float | None  # 0-100, None when the raw value is unavailable
    weight: float
    contribution: (
        float  # normalized_score * weight when available, else 0 (excluded, not penalized)
    )
    status: FactorStatus
    reason: str


@dataclass
class FundamentalData:
    """Raw fundamental inputs. Every field is optional — availability
    varies by data source and by company (especially IPOs). The scorer
    treats a missing field as UNKNOWN, never as zero or as a reason to
    fabricate a value."""

    symbol: str
    as_of: date | None = None

    # Valuation
    pe: float | None = None
    pb: float | None = None
    peg: float | None = None
    ev_ebitda: float | None = None
    market_cap_cr: float | None = None
    dividend_yield_pct: float | None = None

    # Absolute figures (informational — not independently scored; growth %
    # fields above are what feed the scorer). Populated when a provider
    # reports them directly, never derived/computed here.
    revenue_ttm_cr: float | None = None
    net_profit_ttm_cr: float | None = None

    # Growth
    revenue_growth_3y_pct: float | None = None
    revenue_growth_5y_pct: float | None = None
    eps_growth_3y_pct: float | None = None
    profit_growth_3y_pct: float | None = None

    # Profitability
    roe_pct: float | None = None
    roce_pct: float | None = None
    operating_margin_pct: float | None = None
    net_margin_pct: float | None = None

    # Financial strength
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    current_ratio: float | None = None

    # Cash flow
    operating_cash_flow_cr: float | None = None
    free_cash_flow_cr: float | None = None

    # Ownership
    promoter_holding_pct: float | None = None
    promoter_holding_change_pct: float | None = None
    promoter_pledge_pct: float | None = None
    fii_holding_pct: float | None = None
    dii_holding_pct: float | None = None

    # Free-text risk/quality notes a real provider might supply (auditor
    # concerns, litigation, dilution, one-time gains, ...) — never inferred,
    # only ever passed through from whatever the provider actually reports.
    risk_notes: list[str] = field(default_factory=list)

    # Provenance (value/source/period/status) for whichever fields above a
    # provider actually populated — see `FieldSnapshot`. Optional: a
    # provider may set plain float fields without this and everything
    # still works (the scorer never reads it); only the explainability
    # surfaces (dashboard/API/Telegram) consume it.
    field_snapshots: dict[str, FieldSnapshot] = field(default_factory=dict)


@dataclass
class FundamentalScoreResult:
    tier: FundamentalTier
    score: float | None  # None only when tier == UNKNOWN
    data_completeness_pct: float
    factors: list[FundamentalFactor]
    positive_reasons: list[str]
    negative_reasons: list[str]
    unknown_reasons: list[str]
    risk_flags: list[str]
