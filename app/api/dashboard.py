"""Serves the single-page dashboard (app/web/dashboard.html).

The HTML/JS itself is a static asset with no server-side templating —
it's unauthenticated to *fetch* (a browser must be able to load the
login form), but everything it does after that goes through the
session-cookie-protected JSON API (`/auth/*`, `/candidates*`,
`/admin/users*`), which is where access control actually lives.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")
