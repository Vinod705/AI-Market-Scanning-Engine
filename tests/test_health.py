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
        "feature_engine",
        "scanner",
        "decision_engine",
        "alert_queue",
        "whatsapp",
    ):
        assert key in body


@pytest.mark.asyncio
async def test_health_reports_unavailable_components_without_lifespan(client: AsyncClient) -> None:
    """The test client doesn't run the app's lifespan, so provider/scheduler/
    queue/whatsapp never get attached to app.state — the endpoint should
    degrade gracefully rather than raising."""
    response = await client.get("/health")
    body = response.json()
    assert body["scanner"] == "unavailable"
    assert body["alert_queue"] == "unavailable"
    assert body["whatsapp"] == "unavailable"
