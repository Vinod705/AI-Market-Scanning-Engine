"""Pydantic response schemas for the /alerts and /decisions APIs."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: int
    symbol: str
    scanner_name: str
    signal_type: str
    decision: str
    score: Decimal
    quality: str
    entry_reference: Decimal | None
    breakout_level: Decimal | None
    support_level: Decimal | None
    resistance_level: Decimal | None
    reason: str
    status: str
    fingerprint: str
    signal_date: date
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


class AlertStatusOut(BaseModel):
    total_alerts: int
    sent_count: int
    pending_count: int
    failed_count: int
    last_alert_at: datetime | None


class RuleResultOut(BaseModel):
    rule_name: str
    status: str
    actual_value: str | None
    required_value: str | None
    reason: str


class DecisionOut(BaseModel):
    symbol: str
    scanner_name: str
    signal_type: str
    decision: str
    score: float
    quality: str | None
    passed_rules: list[str]
    failed_rules: list[str]
    warnings: list[str]
    timestamp: datetime
    rules: list[RuleResultOut]
