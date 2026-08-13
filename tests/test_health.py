"""Tests for the /health endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200_with_subsystem_statuses(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] in {"healthy", "degraded"}
    for key in (
        "database",
        "market_data",
        "scheduler",
        "feature_engine",
        "scanner",
        "decision_engine",
        "ingestion_worker",
        "market_data_feed",
        "alert_queue",
        "telegram",
        "telegram_ipo",
        "telegram_fno",
        "tradingview",
        "trendlyne_mcp",
        "fundamental_queue",
        "cpu_percent",
        "memory_used_mb",
        "memory_total_mb",
        "memory_percent",
        "disk_used_gb",
        "disk_total_gb",
        "disk_percent",
    ):
        assert key in body


@pytest.mark.asyncio
async def test_health_reports_system_metrics_without_secrets(client: AsyncClient) -> None:
    """The dashboard's live health strip reads these fields — must be
    real numbers (or an honest None), and the response must never leak
    the Trendlyne MCP URL/token or any other credential."""
    response = await client.get("/health")
    body = response.json()

    assert body["memory_total_mb"] is None or body["memory_total_mb"] > 0
    assert body["disk_total_gb"] is None or body["disk_total_gb"] > 0

    raw = response.text.lower()
    assert "token" not in raw
    assert "mcp.trendlyne.com" not in raw


@pytest.mark.asyncio
async def test_health_reports_unavailable_components_without_lifespan(client: AsyncClient) -> None:
    """The test client doesn't run the app's lifespan, so provider/scheduler/
    queue/telegram never get attached to app.state — the endpoint should
    degrade gracefully rather than raising."""
    response = await client.get("/health")
    body = response.json()
    assert body["scanner"] == "unavailable"
    assert body["ingestion_worker"] == "unavailable"
    assert body["alert_queue"] == "unavailable"
    assert body["telegram"] == "unavailable"
    assert body["telegram_ipo"] == "unavailable"
    assert body["telegram_fno"] == "unavailable"
    assert body["tradingview"] == "unavailable"
    assert body["fundamental_queue"] == "not_configured"


@pytest.mark.asyncio
async def test_health_reports_trendlyne_mcp_not_configured(client: AsyncClient) -> None:
    """No Trendlyne MCP integration exists in this deployment (Phase 7
    discovery finding) — must be reported honestly, never as healthy."""
    response = await client.get("/health")
    assert response.json()["trendlyne_mcp"] == "not_configured"
