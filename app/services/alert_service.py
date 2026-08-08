"""Read-side service backing the /alerts API endpoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository
from app.schemas.alerts import AlertOut, AlertStatusOut


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self._alert_repo = AlertRepository(session)
        self._symbol_repo = SymbolRepository(session)

    async def get_status(self) -> AlertStatusOut:
        summary = await self._alert_repo.get_status_summary()
        return AlertStatusOut(
            total_alerts=summary.total_alerts,
            sent_count=summary.sent_count,
            pending_count=summary.pending_count,
            failed_count=summary.failed_count,
            last_alert_at=summary.last_alert_at,
        )

    async def list_recent(self, *, symbol: str | None = None, limit: int = 50) -> list[AlertOut]:
        symbol_id = None
        if symbol is not None:
            symbol_row = await self._symbol_repo.get_by_symbol(symbol)
            if symbol_row is None:
                return []
            symbol_id = symbol_row.id

        rows = await self._alert_repo.list_recent(symbol_id=symbol_id, limit=limit)
        return await self._to_schema_list(rows)

    async def get_by_id(self, alert_id: int) -> AlertOut | None:
        alert = await self._alert_repo.get_by_id(alert_id)
        if alert is None:
            return None
        results = await self._to_schema_list([alert])
        return results[0]

    # --- internals -----------------------------------------------------

    async def _to_schema_list(self, rows: list[Alert]) -> list[AlertOut]:
        if not rows:
            return []
        symbols = await self._symbol_repo.list_by_ids(list({row.symbol_id for row in rows}))
        symbol_names = {symbol.id: symbol.symbol for symbol in symbols}

        return [
            AlertOut(
                id=row.id,
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                scanner_name=row.scanner_name,
                signal_type=row.signal_type,
                decision=row.decision,
                score=row.score,
                quality=row.quality,
                entry_reference=row.entry_reference,
                breakout_level=row.breakout_level,
                support_level=row.support_level,
                resistance_level=row.resistance_level,
                reason=row.reason,
                status=row.status,
                fingerprint=row.fingerprint,
                signal_date=row.signal_date,
                created_at=row.created_at,
                updated_at=row.updated_at,
                expires_at=row.expires_at,
            )
            for row in rows
        ]
