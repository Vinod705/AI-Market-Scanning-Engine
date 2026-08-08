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
