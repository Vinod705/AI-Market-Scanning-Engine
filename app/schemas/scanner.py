"""Pydantic response schemas for the /scanner API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class ScannerResultOut(BaseModel):
    symbol: str
    scanner_name: str
    date: date
    score: Decimal
    status: str
    reason: str
    feature_snapshot: dict[str, object]
    created_at: datetime


class ScannerRunOut(BaseModel):
    scanner_name: str
    start_time: datetime
    finish_time: datetime | None
    duration: float | None
    symbols_scanned: int
    qualified_count: int
    rejected_count: int
    error_count: int


class ScannerStatusOut(BaseModel):
    scanner_name: str
    total_results: int
    qualified_count: int
    last_run_at: datetime | None
