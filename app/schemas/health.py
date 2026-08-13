"""Pydantic schemas for health check responses."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    database: str = "unknown"
    market_data: str = "unknown"
    # APScheduler itself — daily/symbol-refresh collection, universe
    # refresh, fundamental queue, alert expiry, digest. Not the hot
    # feature/scanner/decision path anymore — see pipeline_worker-backed
    # fields below.
    scheduler: str = "unknown"
    feature_engine: str = "unknown"
    scanner: str = "unknown"
    decision_engine: str = "unknown"
    ingestion_worker: str = "unknown"
    alert_queue: str = "unknown"
    telegram: str = "unknown"
    telegram_ipo: str = "unknown"
    telegram_fno: str = "unknown"
    tradingview: str = "unknown"
    trendlyne_mcp: str = "unknown"
    fundamental_queue: str = "unknown"
    # OS-level resource snapshot for the dashboard's health strip (see
    # app.core.system_metrics) — None when unavailable, never fabricated.
    cpu_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    memory_percent: float | None = None
    disk_used_gb: float | None = None
    disk_total_gb: float | None = None
    disk_percent: float | None = None
