"""Tests for app.core.system_metrics — the stdlib-only OS resource
snapshot backing the dashboard's live health strip."""

from app.core.system_metrics import get_system_metrics


async def test_get_system_metrics_returns_sane_values_on_this_host() -> None:
    """Runs on real Linux CI/container hosts (this project only ships a
    Linux image — see Dockerfile) — every field should resolve, not just
    degrade to None, and stay within physically sane bounds."""
    metrics = await get_system_metrics()

    assert metrics.cpu_percent is None or 0.0 <= metrics.cpu_percent <= 100.0

    assert metrics.memory_total_mb is not None
    assert metrics.memory_used_mb is not None
    assert metrics.memory_percent is not None
    assert 0.0 <= metrics.memory_used_mb <= metrics.memory_total_mb
    assert 0.0 <= metrics.memory_percent <= 100.0

    assert metrics.disk_total_gb is not None
    assert metrics.disk_used_gb is not None
    assert metrics.disk_percent is not None
    assert 0.0 <= metrics.disk_used_gb <= metrics.disk_total_gb
    assert 0.0 <= metrics.disk_percent <= 100.0


async def test_get_system_metrics_never_raises() -> None:
    """A dashboard diagnostic must never be the thing that breaks
    /health — even if called repeatedly in quick succession."""
    for _ in range(3):
        await get_system_metrics()
