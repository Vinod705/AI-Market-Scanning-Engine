"""Market data read API."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.schemas.market import LatestPriceOut, MarketStatusOut, SymbolOut
from app.services.market_service import MarketService

router = APIRouter(prefix="/market", tags=["market"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/health")
async def market_health() -> dict[str, str]:
    """Liveness check for the market data module specifically."""
    return {"status": "ok"}


@router.get("/status", response_model=MarketStatusOut)
async def get_market_status(session: DbSession) -> MarketStatusOut:
    service = MarketService(session)
    status = await service.get_status()
    if status is None:
        raise NotFoundError("Market status has not been recorded yet")
    return status


@router.get("/symbols", response_model=list[SymbolOut])
async def list_symbols(session: DbSession) -> list[SymbolOut]:
    service = MarketService(session)
    return await service.list_symbols()


@router.get("/latest/{symbol}", response_model=LatestPriceOut)
async def get_latest_price(symbol: str, session: DbSession) -> LatestPriceOut:
    service = MarketService(session)
    latest = await service.get_latest(symbol.upper())
    if latest is None:
        raise NotFoundError(f"No price data for symbol '{symbol}'")
    return latest
