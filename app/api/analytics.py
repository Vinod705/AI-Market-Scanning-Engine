"""Read-only analytics API backing the dashboard's Market Intelligence
section (Phase 15) — market regime, sector rotation/RRG, top momentum
candidates, volume/RVOL leaders, OI buildup, trigger history, and
fundamentals coverage. Every route here only reads a table an existing
scheduled job or engine already populated; no route computes anything
itself (see `app.services.analytics_service`'s own docstring) — a
dashboard request must never trigger a live scanner-style calculation.

Same session-cookie auth as every other read API in this project (see
`app.api.alerts`'s own docstring) — anonymous visitors must not be able
to read this either.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.schemas.analytics import (
    FundamentalsCoverageOut,
    LiveTriggerOut,
    MarketRegimeOut,
    MomentumCandidateOut,
    MomentumTransitionOut,
    OiBuildupOut,
    ProviderHealthOut,
    SectorRrgOut,
    VolumeLeaderOut,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/market-regime", response_model=MarketRegimeOut | None)
async def market_regime(session: DbSession) -> MarketRegimeOut | None:
    return await AnalyticsService(session, get_settings()).get_market_regime()


@router.get("/sector-rrg", response_model=list[SectorRrgOut])
async def sector_rrg(session: DbSession) -> list[SectorRrgOut]:
    return await AnalyticsService(session, get_settings()).get_sector_rrg()


@router.get("/momentum/candidates", response_model=list[MomentumCandidateOut])
async def momentum_candidates(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[MomentumCandidateOut]:
    return await AnalyticsService(session, get_settings()).get_momentum_candidates(limit)


@router.get("/momentum/history", response_model=list[MomentumTransitionOut])
async def momentum_history(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[MomentumTransitionOut]:
    return await AnalyticsService(session, get_settings()).get_momentum_history(limit)


@router.get("/volume-leaders", response_model=list[VolumeLeaderOut])
async def volume_leaders(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[VolumeLeaderOut]:
    return await AnalyticsService(session, get_settings()).get_volume_leaders(limit)


@router.get("/oi-buildup", response_model=list[OiBuildupOut])
async def oi_buildup(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[OiBuildupOut]:
    return await AnalyticsService(session, get_settings()).get_oi_buildup(limit)


@router.get("/fundamentals-coverage", response_model=FundamentalsCoverageOut)
async def fundamentals_coverage(session: DbSession) -> FundamentalsCoverageOut:
    return await AnalyticsService(session, get_settings()).get_fundamentals_coverage()


@router.get("/live-triggers", response_model=list[LiveTriggerOut])
async def live_triggers(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[LiveTriggerOut]:
    """Phase 16 operational validation — recent live triggers from
    `app.scheduler.momentum_pipeline_jobs`, with real observed subsequent
    price behavior and delivery latency. Read-only, same as every other
    route here."""
    return await AnalyticsService(session, get_settings()).get_live_triggers(limit)


@router.get("/provider-health", response_model=ProviderHealthOut)
async def provider_health(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> ProviderHealthOut:
    """Phase 16 operational validation — recent REST collector runs and
    Upstox WebSocket connect/reconnect cycles, straight from
    `collector_logs`/`market_data_feed_logs`."""
    return await AnalyticsService(session, get_settings()).get_provider_health(limit)
