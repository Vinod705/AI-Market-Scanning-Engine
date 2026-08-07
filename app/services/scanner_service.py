"""Read-side service backing the /scanner API endpoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository, ScannerRunRepository
from app.schemas.scanner import ScannerResultOut, ScannerRunOut, ScannerStatusOut


class ScannerService:
    def __init__(self, session: AsyncSession) -> None:
        self._symbol_repo = SymbolRepository(session)
        self._result_repo = ScannerResultRepository(session)
        self._run_repo = ScannerRunRepository(session)

    async def get_status(self) -> list[ScannerStatusOut]:
        names = await self._run_repo.list_scanner_names()
        summaries = []
        for name in names:
            summary = await self._result_repo.get_status_summary(name)
            summaries.append(
                ScannerStatusOut(
                    scanner_name=summary.scanner_name,
                    total_results=summary.total_results,
                    qualified_count=summary.qualified_count,
                    last_run_at=summary.last_run_at,
                )
            )
        return summaries

    async def list_results(
        self, *, scanner_name: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[ScannerResultOut]:
        rows = await self._result_repo.list_results(
            scanner_name=scanner_name, status=status, limit=limit
        )
        return await self._to_schema_list(rows)

    async def get_results_for_symbol(
        self, symbol: str, limit: int = 50
    ) -> list[ScannerResultOut] | None:
        symbol_row = await self._symbol_repo.get_by_symbol(symbol)
        if symbol_row is None:
            return None
        rows = await self._result_repo.get_for_symbol(symbol_row.id, limit)
        return await self._to_schema_list(rows, symbol_row.symbol)

    async def list_runs(
        self, *, scanner_name: str | None = None, limit: int = 20
    ) -> list[ScannerRunOut]:
        rows = await self._run_repo.get_recent(scanner_name=scanner_name, limit=limit)
        return [
            ScannerRunOut(
                scanner_name=row.scanner_name,
                start_time=row.start_time,
                finish_time=row.finish_time,
                duration=row.duration,
                symbols_scanned=row.symbols_scanned,
                qualified_count=row.qualified_count,
                rejected_count=row.rejected_count,
                error_count=row.error_count,
            )
            for row in rows
        ]

    # --- internals -----------------------------------------------------

    async def _to_schema_list(self, rows: list, known_symbol: str | None = None) -> list[ScannerResultOut]:  # type: ignore[type-arg]
        if not rows:
            return []

        if known_symbol is not None:
            symbol_names = dict.fromkeys((row.symbol_id for row in rows), known_symbol)
        else:
            symbols = await self._symbol_repo.list_by_ids(list({row.symbol_id for row in rows}))
            symbol_names = {symbol.id: symbol.symbol for symbol in symbols}

        return [
            ScannerResultOut(
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                scanner_name=row.scanner_name,
                date=row.date,
                score=row.score,
                status=row.status,
                reason=row.reason,
                feature_snapshot=row.feature_snapshot,
                created_at=row.created_at,
            )
            for row in rows
        ]
