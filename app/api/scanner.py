"""Scanner engine read API — exposes scan results/runs computed by the scheduler."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.schemas.scanner import ScannerResultOut, ScannerRunOut, ScannerStatusOut
from app.services.scanner_service import ScannerService

router = APIRouter(prefix="/scanner", tags=["scanner"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/status", response_model=list[ScannerStatusOut])
async def get_scanner_status(session: DbSession) -> list[ScannerStatusOut]:
    return await ScannerService(session).get_status()


@router.get("/results", response_model=list[ScannerResultOut])
async def list_scanner_results(
    session: DbSession,
    scanner_name: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ScannerResultOut]:
    return await ScannerService(session).list_results(
        scanner_name=scanner_name, status=status, limit=limit
    )


@router.get("/results/{symbol}", response_model=list[ScannerResultOut])
async def get_scanner_results_for_symbol(
    symbol: str, session: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[ScannerResultOut]:
    result = await ScannerService(session).get_results_for_symbol(symbol.upper(), limit)
    if result is None:
        raise NotFoundError(f"Unknown symbol '{symbol}'")
    return result


@router.get("/runs", response_model=list[ScannerRunOut])
async def list_scanner_runs(
    session: DbSession,
    scanner_name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ScannerRunOut]:
    return await ScannerService(session).list_runs(scanner_name=scanner_name, limit=limit)
