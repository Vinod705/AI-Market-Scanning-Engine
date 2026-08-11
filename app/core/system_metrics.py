"""Lightweight, dependency-free OS-level resource snapshot for the
/health endpoint's dashboard strip — stdlib only (no psutil, no new
dependency), since this is a small read-only diagnostic, not a metrics/
monitoring subsystem. Linux-only sources (/proc/stat, /proc/meminfo);
degrades to None fields rather than raising if unavailable.
"""

import asyncio
import shutil
from dataclasses import dataclass

_CPU_SAMPLE_INTERVAL_SECONDS = 0.1


@dataclass
class SystemMetrics:
    cpu_percent: float | None
    memory_used_mb: float | None
    memory_total_mb: float | None
    memory_percent: float | None
    disk_used_gb: float | None
    disk_total_gb: float | None
    disk_percent: float | None


def _read_cpu_fields() -> list[int] | None:
    """The aggregate `cpu ` line of /proc/stat: cumulative jiffies since
    boot for [user, nice, system, idle, iowait, irq, softirq, steal, ...]."""
    try:
        with open("/proc/stat", encoding="ascii") as f:
            line = f.readline()
        if not line.startswith("cpu "):
            return None
        return [int(x) for x in line.split()[1:]]
    except (OSError, ValueError, IndexError):
        return None


async def _cpu_percent() -> float | None:
    """Real CPU utilization, sampled as a short /proc/stat delta — the
    same technique `top`/`psutil` use — rather than approximated from
    load average. Load average alone reads misleadingly high on a
    heavily oversubscribed host: Linux counts a process as "load"
    whenever it's runnable-but-waiting, which includes processes stuck
    behind hypervisor steal time, not just genuine CPU-bound work. On a
    free-tier VM where steal time can regularly run 60-80%, that made
    this metric show ~100% even when this VM's actual CPU usage was
    ~20%. `steal` is deliberately excluded from "busy" below: it's CPU
    the hypervisor denied this VM, not work our own processes are doing,
    so counting it as "our CPU usage" would just recreate the same
    misleading reading in a different shape.

    Uses a short `asyncio.sleep` for the delta window so this never
    blocks the event loop — /health is polled automatically by the
    dashboard and must stay fast regardless.
    """
    first = _read_cpu_fields()
    if first is None or len(first) < 7:
        return None
    await asyncio.sleep(_CPU_SAMPLE_INTERVAL_SECONDS)
    second = _read_cpu_fields()
    if second is None or len(second) < 7:
        return None

    delta = [b - a for a, b in zip(first, second, strict=False)]
    total = sum(delta)
    if total <= 0:
        return None
    # user + nice + system + irq + softirq — real work done on this
    # guest. Excludes idle, iowait, and steal.
    busy = delta[0] + delta[1] + delta[2] + delta[5] + delta[6]
    return round(min(100.0, max(0.0, (busy / total) * 100.0)), 1)


def _memory() -> tuple[float | None, float | None, float | None]:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.strip().split()[0])
        total_kb = info.get("MemTotal")
        available_kb = info.get("MemAvailable")
        if not total_kb or available_kb is None:
            return None, None, None
        used_kb = total_kb - available_kb
        percent = round((used_kb / total_kb) * 100.0, 1)
        return round(used_kb / 1024, 1), round(total_kb / 1024, 1), percent
    except (OSError, ValueError, IndexError):
        return None, None, None


def _disk() -> tuple[float | None, float | None, float | None]:
    try:
        usage = shutil.disk_usage("/")
        if usage.total == 0:
            return None, None, None
        used_gb = (usage.total - usage.free) / (1024**3)
        total_gb = usage.total / (1024**3)
        percent = round((used_gb / total_gb) * 100.0, 1)
        return round(used_gb, 1), round(total_gb, 1), percent
    except OSError:
        return None, None, None


async def get_system_metrics() -> SystemMetrics:
    memory_used_mb, memory_total_mb, memory_percent = _memory()
    disk_used_gb, disk_total_gb, disk_percent = _disk()
    return SystemMetrics(
        cpu_percent=await _cpu_percent(),
        memory_used_mb=memory_used_mb,
        memory_total_mb=memory_total_mb,
        memory_percent=memory_percent,
        disk_used_gb=disk_used_gb,
        disk_total_gb=disk_total_gb,
        disk_percent=disk_percent,
    )
