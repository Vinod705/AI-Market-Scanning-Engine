"""Read-only Phase 6 candidate-explainability API.

Backs the dashboard — every field here is a read or arithmetic
derivation of data the scanner/decision engine already computed. No
order placement, no automatic trading: see `app.services.candidate_service`.

Every route requires a logged-in session (ADMIN or VIEWER) — this is
exactly the data the Phase 6 "friend/viewer" spec means by "IPO
candidates, F&O candidates, ... Decision breakdown"; it must not be
readable by an anonymous internet visitor.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config.settings import get_settings
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.schemas.candidates import CandidateExplainOut, CandidateSummaryOut
from app.services.candidate_service import CandidateService

router = APIRouter(tags=["candidates"], dependencies=[Depends(get_current_user)])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/candidates", response_model=list[CandidateSummaryOut])
async def list_candidates(
    session: DbSession,
    universe: str | None = None,
    alert_category: str | None = None,
    status: str = "qualified",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CandidateSummaryOut]:
    return await CandidateService(session, get_settings()).list_candidates(
        universe=universe.upper() if universe else None,
        alert_category=alert_category.upper() if alert_category else None,
        status=status,
        limit=limit,
    )


@router.get("/candidates/{symbol}/explain", response_model=CandidateExplainOut)
async def explain_candidate(symbol: str, session: DbSession) -> CandidateExplainOut:
    result = await CandidateService(session, get_settings()).get_explain(symbol.upper())
    if result is None:
        raise NotFoundError(f"No IPO/F&O candidate result for symbol '{symbol}'")
    return result
