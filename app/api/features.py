"""Feature store read API — scanners consume features from here, never recompute them."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.schemas.features import FeatureHistoryOut, FeatureStatusOut, LatestFeaturesOut
from app.services.feature_service import FeatureService

router = APIRouter(prefix="/features", tags=["features"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/status", response_model=FeatureStatusOut)
async def get_feature_status(session: DbSession) -> FeatureStatusOut:
    return await FeatureService(session).get_status()


@router.get("/latest/{symbol}", response_model=LatestFeaturesOut)
async def get_latest_features(symbol: str, session: DbSession) -> LatestFeaturesOut:
    result = await FeatureService(session).get_latest(symbol.upper())
    if result is None:
        raise NotFoundError(f"No features for symbol '{symbol}'")
    return result


@router.get("/history/{symbol}", response_model=FeatureHistoryOut)
async def get_feature_history(
    symbol: str, session: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> FeatureHistoryOut:
    result = await FeatureService(session).get_history(symbol.upper(), limit)
    if result is None:
        raise NotFoundError(f"No features for symbol '{symbol}'")
    return result
