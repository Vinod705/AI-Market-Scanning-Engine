"""Admin-only diagnostics for the Fundamental Queue — pending/completed/
rate-limited/failed/cached counts, current batch size, last request time,
and Trendlyne status. Never exposes the MCP URL/token; only counts and
timestamps (see app.fundamentals.queue_service.FundamentalQueueService).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_admin
from app.auth.models import AuthenticatedUser
from app.fundamentals.queue_service import FundamentalQueueService
from app.schemas.queue import FundamentalQueueStatusOut

router = APIRouter(prefix="/admin", tags=["admin"])

Admin = Annotated[AuthenticatedUser, Depends(require_admin)]


@router.get("/fundamental-queue", response_model=FundamentalQueueStatusOut)
async def fundamental_queue_status(request: Request, _admin: Admin) -> FundamentalQueueStatusOut:
    queue_service = getattr(request.app.state, "fundamental_queue", None)
    if not isinstance(queue_service, FundamentalQueueService):
        return FundamentalQueueStatusOut(
            pending=0,
            completed_today=0,
            rate_limited_today=0,
            failed_today=0,
            cached_today=0,
            batch_size=0,
            is_paused=False,
            paused_until=None,
            last_request_at=None,
            requests_today=0,
            daily_budget=0,
            trendlyne_status="NOT_CONFIGURED",
        )
    summary = await queue_service.get_status_summary()
    return FundamentalQueueStatusOut(
        pending=summary.pending,
        completed_today=summary.completed_today,
        rate_limited_today=summary.rate_limited_today,
        failed_today=summary.failed_today,
        cached_today=summary.cached_today,
        batch_size=summary.batch_size,
        is_paused=summary.is_paused,
        paused_until=summary.paused_until,
        last_request_at=summary.last_request_at,
        requests_today=summary.requests_today,
        daily_budget=summary.daily_budget,
        trendlyne_status=summary.trendlyne_status,
    )
