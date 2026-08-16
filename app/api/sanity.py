"""Project Sanity Check API — a deliberately separate, standalone
diagnostic surface. Not part of the existing trading dashboard's
`/candidates`, `/analytics`, `/scanner`, etc. routes, not linked from
`app/web/dashboard.html`, and never triggers any scan/decision/alert
work itself — every route here only ever reads.

Unauthenticated on purpose, same reasoning `/health` already documents:
this is a liveness/diagnostic surface (conventionally public in most
deployments), not trading data or account access.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.auth.redis_client import redis_client
from app.config.settings import get_settings
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal, engine
from app.sanity.cleanup_audit import run_cleanup_audit
from app.sanity.paths import repo_root
from app.sanity.service import SanityService

router = APIRouter(prefix="/sanity", tags=["sanity"])

_SANITY_HTML = Path(__file__).resolve().parent.parent / "web" / "sanity.html"


@router.get("", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
async def sanity_dashboard() -> FileResponse:
    """The separate Project Sanity Dashboard page (app/web/sanity.html) —
    a standalone static page with no shared nav/JS with
    app/web/dashboard.html, per this project's explicit requirement not
    to touch the existing trading dashboard."""
    return FileResponse(_SANITY_HTML, media_type="text/html")


@router.get("/status")
async def get_sanity_status() -> dict[str, object]:
    settings = get_settings()
    service = SanityService(
        AsyncSessionLocal,
        engine,
        settings,
        redis_client,
        is_market_open=MarketStatusUpdater.is_market_open(),
    )
    report = await service.run()
    return report.to_dict()


@router.get("/cleanup")
async def get_cleanup_audit() -> dict[str, object]:
    """Filesystem scan — slower than /status, so kept as its own
    endpoint rather than baked into every status poll. AUDIT ONLY: never
    deletes or modifies anything, see app.sanity.cleanup_audit's
    module docstring."""
    report = run_cleanup_audit(repo_root())
    return {
        "scanned_files": report.scanned_files,
        "unused_count": report.unused_count,
        "duplicate_count": report.duplicate_count,
        "legacy_count": report.legacy_count,
        "unused_dependency_count": len(report.unused_dependency_candidates),
        "findings": [
            {
                "path": f.path,
                "classification": f.classification.value,
                "confidence": f.confidence,
                "reason": f.reason,
                "references": f.references,
                "recommended_action": f.recommended_action,
            }
            for f in report.findings
        ],
        "unused_dependency_candidates": [
            {
                "path": f.path,
                "classification": f.classification.value,
                "confidence": f.confidence,
                "reason": f.reason,
                "recommended_action": f.recommended_action,
            }
            for f in report.unused_dependency_candidates
        ],
    }
