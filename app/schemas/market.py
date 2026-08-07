"""Pydantic response schemas for the /market API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SymbolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    exchange: str
    company_name: str | None
    sector: str | None
    industry: str | None
    is_active: bool


class MarketStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    market_open: bool
    provider_connected: bool
    last_update: datetime | None
    last_success: datetime | None
    last_failure: datetime | None


class LatestPriceOut(BaseModel):
    symbol: str
    source: str  # "intraday" or "daily"
    timestamp: datetime | date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None
