"""Pydantic response schemas for the read-only Phase 6 candidate-explainability API.

`CandidateExplainOut` is the single, shared explainability shape for
IPO and F&O alike — see `app.candidates.explainer` module docstring for
why there is exactly one scoring/explanation system, not two.
"""

from datetime import date, datetime

from pydantic import BaseModel


class FieldSourceOut(BaseModel):
    field_name: str
    value: float | None
    source: str | None
    period: str | None
    status: str
    alternates: list[tuple[str, float]]


class FactorOut(BaseModel):
    factor_name: str
    category: str
    value: str | None
    normalized_score: float | None
    weight: float
    contribution: float
    status: str
    reason: str


class CategoryBreakdownOut(BaseModel):
    category: str
    label: str
    score: float
    max_score: float
    factors: list[FactorOut]


class DataFreshnessOut(BaseModel):
    scan_date: date
    days_old: int
    is_fresh: bool
    max_age_days: int


class OverallScoreBreakdownOut(BaseModel):
    technical_weight: float
    fundamental_weight: float
    technical_contribution: float
    fundamental_contribution: float | None
    overall_score: float
    note: str | None


class DecisionRuleOut(BaseModel):
    rule_name: str
    status: str
    actual_value: str | None
    required_value: str | None
    reason: str


class DecisionExplanationOut(BaseModel):
    why_this_stock: str
    why_now: str
    what_confirms: list[str]
    what_has_not_been_confirmed: list[str]
    risks: list[str]


class CandidateSummaryOut(BaseModel):
    symbol: str
    universe: str
    scanner_name: str
    alert_category: str | None
    setup_state: str | None
    status: str
    price: float | None
    overall_score: float
    technical_score: float
    fundamental_score: float | None
    quality: str
    scan_date: date
    scanner_sources: list[str]
    scanner_confirmation_count: int


class CandidateExplainOut(BaseModel):
    symbol: str
    universe: str
    scanner_name: str
    alert_category: str | None
    setup_state: str | None
    instrument_type: str

    price: float | None
    breakout_level: float | None
    support_level: float | None
    resistance_level: float | None

    fundamental_score: float | None
    technical_score: float
    overall_score: float
    quality: str
    overall_score_breakdown: OverallScoreBreakdownOut
    scanner_sources: list[str]
    scanner_confirmation_count: int

    fundamental_reasons: list[str]
    technical_reasons: list[str]
    positive_factors: list[FactorOut]
    negative_factors: list[FactorOut]
    risk_flags: list[str]

    passed_rules: list[str]
    failed_rules: list[str]

    fundamental_data_completeness_pct: float
    technical_data_completeness_pct: float
    data_freshness: DataFreshnessOut

    technical_breakdown: list[CategoryBreakdownOut]
    fundamental_breakdown: list[CategoryBreakdownOut]
    fundamental_unavailable_reason: str | None
    fundamental_field_sources: list[FieldSourceOut]

    decision: str
    decision_quality: str | None
    decision_rules: list[DecisionRuleOut]

    explanation: DecisionExplanationOut

    scan_date: date
    timestamp: datetime
