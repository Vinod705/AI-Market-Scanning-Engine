"""Repository layer for the scanner engine: `scanner_results`, `scanner_runs`, `scanner_logs`."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scanner_log import ScannerLog
from app.models.scanner_result import ScannerResult
from app.models.scanner_run import ScannerRun


@dataclass
class ScannerStatusSummary:
    scanner_name: str
    total_results: int
    qualified_count: int
    last_run_at: datetime_ | None


class ScannerResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        symbol_id: int,
        scanner_name: str,
        date: date_,
        score: Decimal,
        status: str,
        reason: str,
        feature_snapshot: Mapping[str, object],
    ) -> ScannerResult:
        """Insert or update the (symbol, scanner, date) row — this is the whole
        "no duplicate alert" mechanism: a second scan on the same day updates
        the existing verdict instead of creating a new alert."""
        stmt = select(ScannerResult).where(
            ScannerResult.symbol_id == symbol_id,
            ScannerResult.scanner_name == scanner_name,
            ScannerResult.date == date,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ScannerResult(symbol_id=symbol_id, scanner_name=scanner_name, date=date)
            self._session.add(row)

        row.score = score
        row.status = status
        row.reason = reason
        row.feature_snapshot = dict(feature_snapshot)

        await self._session.flush()
        return row

    async def exists_for_date(self, symbol_id: int, scanner_name: str, date: date_) -> bool:
        stmt = select(ScannerResult.id).where(
            ScannerResult.symbol_id == symbol_id,
            ScannerResult.scanner_name == scanner_name,
            ScannerResult.date == date,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def list_results(
        self,
        *,
        scanner_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ScannerResult]:
        stmt = select(ScannerResult)
        if scanner_name is not None:
            stmt = stmt.where(ScannerResult.scanner_name == scanner_name)
        if status is not None:
            stmt = stmt.where(ScannerResult.status == status)
        stmt = stmt.order_by(ScannerResult.date.desc(), ScannerResult.score.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_for_symbol(self, symbol_id: int, limit: int = 50) -> list[ScannerResult]:
        stmt = (
            select(ScannerResult)
            .where(ScannerResult.symbol_id == symbol_id)
            .order_by(ScannerResult.date.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_status_summary(self, scanner_name: str) -> ScannerStatusSummary:
        stmt = select(
            func.count(ScannerResult.id),
            func.count(ScannerResult.id).filter(ScannerResult.status == "qualified"),
            func.max(ScannerResult.updated_at),
        ).where(ScannerResult.scanner_name == scanner_name)
        total, qualified, last_run_at = (await self._session.execute(stmt)).one()
        return ScannerStatusSummary(
            scanner_name=scanner_name,
            total_results=total or 0,
            qualified_count=qualified or 0,
            last_run_at=last_run_at,
        )


class ScannerRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, scanner_name: str, start_time: datetime_) -> ScannerRun:
        run = ScannerRun(scanner_name=scanner_name, start_time=start_time)
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish(
        self,
        run: ScannerRun,
        *,
        finish_time: datetime_,
        symbols_scanned: int,
        qualified_count: int,
        rejected_count: int,
        error_count: int,
    ) -> None:
        run.finish_time = finish_time
        run.duration = (finish_time - run.start_time).total_seconds()
        run.symbols_scanned = symbols_scanned
        run.qualified_count = qualified_count
        run.rejected_count = rejected_count
        run.error_count = error_count
        await self._session.flush()

    async def list_scanner_names(self) -> list[str]:
        """Distinct scanner names that have run at least once — used to build
        /scanner/status without the API layer needing the in-process registry."""
        stmt = select(ScannerRun.scanner_name).distinct().order_by(ScannerRun.scanner_name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_recent(
        self, scanner_name: str | None = None, limit: int = 20
    ) -> list[ScannerRun]:
        stmt = select(ScannerRun)
        if scanner_name is not None:
            stmt = stmt.where(ScannerRun.scanner_name == scanner_name)
        stmt = stmt.order_by(ScannerRun.start_time.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())


class ScannerLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        run_id: int,
        scanner_name: str,
        level: str,
        message: str,
        symbol_id: int | None = None,
    ) -> ScannerLog:
        entry = ScannerLog(
            run_id=run_id,
            symbol_id=symbol_id,
            scanner_name=scanner_name,
            level=level,
            message=message,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
