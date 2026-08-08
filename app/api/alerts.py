"""Alert + decision read API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.schemas.alerts import AlertOut, AlertStatusOut, DecisionOut
from app.services.alert_service import AlertService
from app.services.decision_service import DecisionService

router = APIRouter(tags=["alerts"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    session: DbSession, symbol: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[AlertOut]:
    return await AlertService(session).list_recent(
        symbol=symbol.upper() if symbol else None, limit=limit
    )


@router.get("/alerts/recent", response_model=list[AlertOut])
async def recent_alerts(
    session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[AlertOut]:
    return await AlertService(session).list_recent(limit=limit)


@router.get("/alerts/status", response_model=AlertStatusOut)
async def alert_status(session: DbSession) -> AlertStatusOut:
    return await AlertService(session).get_status()


@router.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: int, session: DbSession) -> AlertOut:
    result = await AlertService(session).get_by_id(alert_id)
    if result is None:
        raise NotFoundError(f"No alert with id {alert_id}")
    return result


@router.get("/decisions/{symbol}", response_model=DecisionOut)
async def get_decision(symbol: str, session: DbSession) -> DecisionOut:
    result = await DecisionService(session, get_settings()).get_latest_decision(symbol.upper())
    if result is None:
        raise NotFoundError(f"No scanner result for symbol '{symbol}'")
    return result
