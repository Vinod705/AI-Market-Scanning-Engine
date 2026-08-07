"""Read-side service backing the /features API endpoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import SymbolRepository
from app.schemas.features import (
    DailyFeatureOut,
    FeatureHistoryOut,
    FeatureStatusOut,
    LatestFeaturesOut,
    SessionFeatureOut,
)


class FeatureService:
    def __init__(self, session: AsyncSession) -> None:
        self._symbol_repo = SymbolRepository(session)
        self._daily_repo = DailyFeatureRepository(session)
        self._session_repo = SessionFeatureRepository(session)

    async def get_latest(self, symbol: str) -> LatestFeaturesOut | None:
        symbol_row = await self._symbol_repo.get_by_symbol(symbol)
        if symbol_row is None:
            return None

        daily = await self._daily_repo.get_latest(symbol_row.id)
        session_feature = await self._session_repo.get_latest(symbol_row.id)

        return LatestFeaturesOut(
            symbol=symbol_row.symbol,
            daily=DailyFeatureOut.model_validate(daily) if daily else None,
            session=SessionFeatureOut.model_validate(session_feature) if session_feature else None,
        )

    async def get_history(self, symbol: str, limit: int = 100) -> FeatureHistoryOut | None:
        symbol_row = await self._symbol_repo.get_by_symbol(symbol)
        if symbol_row is None:
            return None

        rows = await self._daily_repo.get_history(symbol_row.id, limit)
        return FeatureHistoryOut(
            symbol=symbol_row.symbol,
            history=[DailyFeatureOut.model_validate(row) for row in rows],
        )

    async def get_status(self) -> FeatureStatusOut:
        summary = await self._daily_repo.get_status_summary()
        return FeatureStatusOut(
            symbols_with_features=summary.symbols_with_features,
            total_daily_feature_rows=summary.total_daily_feature_rows,
            last_computed_at=summary.last_computed_at,
        )
