"""Command-line Project Sanity Check.

    python -m app.sanity              # system health + freshness + pipeline
    python -m app.sanity --cleanup    # also run the file/dependency cleanup audit

Read-only, same as every other entry point into `app.sanity`. Exits
non-zero when the overall system status is STALE, BLOCKED, or FAILED —
suitable for a cron/CI check (`python -m app.sanity || alert-someone`).
"""

import asyncio
import sys
from pathlib import Path

from app.auth.redis_client import redis_client
from app.config.settings import get_settings
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, engine
from app.sanity.cleanup_audit import run_cleanup_audit
from app.sanity.models import SanityReport, SanityStatus
from app.sanity.paths import repo_root
from app.sanity.service import SanityService

_FAILING_STATUSES = {SanityStatus.STALE, SanityStatus.BLOCKED, SanityStatus.FAILED}


def _print_report(report: SanityReport) -> None:
    print(f"SYSTEM STATUS: {report.overall_status.value}")
    ist = report.checked_at_ist.strftime("%Y-%m-%d %H:%M:%S %Z") if report.checked_at_ist else "—"
    print(f"Current IST time:       {ist}")
    print(f"Indian trading date:    {report.current_market_date or '—'}")
    print(f"(checked, UTC: {report.checked_at.isoformat()})")
    print()

    for component in report.components:
        print(f"{component.name:<20} {component.status.value:<10} {component.detail}")
    print()

    if report.scanners:
        print("Scanners:")
        for scanner in report.scanners:
            date_part = f" (latest: {scanner.latest_result_date})" if scanner.latest_result_date else ""
            print(f"  {scanner.scanner_name:<18} {scanner.status.value:<10}{date_part}")
        print()

    print(f"daily_prices         {report.daily_prices_latest_date or '—'}")
    print(f"daily_features       {report.daily_features_latest_date or '—'}")
    print()

    print(f"Critical Issues: {len(report.issues)}")
    for issue in report.issues:
        print(f"  - {issue}")


def _print_cleanup(repo: Path) -> None:
    print()
    print("=" * 60)
    print("PROJECT CLEANUP AUDIT (read-only — nothing was modified)")
    print("=" * 60)
    audit = run_cleanup_audit(repo)
    print(f"Scanned {audit.scanned_files} candidate files.")
    print(
        f"Unused candidates: {audit.unused_count}   "
        f"Duplicate candidates: {audit.duplicate_count}   "
        f"Legacy candidates: {audit.legacy_count}   "
        f"Unused dependency candidates: {len(audit.unused_dependency_candidates)}"
    )
    print()
    for finding in audit.findings:
        print(f"[{finding.classification.value}] ({finding.confidence}) {finding.path}")
        print(f"    {finding.reason}")
        if finding.references:
            print(f"    Also found: {', '.join(finding.references)}")
    for finding in audit.unused_dependency_candidates:
        print(f"[{finding.classification.value}] ({finding.confidence}) {finding.path}")
        print(f"    {finding.reason}")


async def _main() -> int:
    settings = get_settings()
    service = SanityService(
        AsyncSessionLocal,
        engine,
        settings,
        redis_client,
        is_market_open=MarketStatusUpdater.is_market_open(),
    )
    report = await service.run()
    _print_report(report)

    if "--cleanup" in sys.argv:
        _print_cleanup(repo_root())

    return 1 if report.overall_status in _FAILING_STATUSES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
