"""Pydantic schemas for health check responses."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    database: str = "unknown"
    market_data: str = "unknown"
    feature_engine: str = "unknown"
    scanner: str = "unknown"
    decision_engine: str = "unknown"
    alert_queue: str = "unknown"
    telegram: str = "unknown"
