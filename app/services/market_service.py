"""Read-side service backing the /market API endpoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.market_repository import (
    MarketStatusRepository,
    PriceRepository,
    SymbolRepository,
)
from app.schemas.market import LatestPriceOut, MarketStatusOut, SymbolOut


class MarketService:
    """Composes repositories to answer read queries for the API layer."""

    def __init__(self, session: AsyncSession) -> None:
        self._symbol_repo = SymbolRepository(session)
        self._price_repo = PriceRepository(session)
        self._status_repo = MarketStatusRepository(session)

    async def get_status(self) -> MarketStatusOut | None:
        status = await self._status_repo.get()
        if status is None:
            return None
        return MarketStatusOut.model_validate(status)

    async def list_symbols(self) -> list[SymbolOut]:
        symbols = await self._symbol_repo.list_active()
        return [SymbolOut.model_validate(s) for s in symbols]

    async def get_latest(self, symbol: str) -> LatestPriceOut | None:
        symbol_row = await self._symbol_repo.get_by_symbol(symbol)
        if symbol_row is None:
            return None

        intraday = await self._price_repo.get_latest_intraday(symbol_row.id)
        if intraday is not None:
            return LatestPriceOut(
                symbol=symbol_row.symbol,
                source="intraday",
                timestamp=intraday.datetime,
                open=intraday.open,
                high=intraday.high,
                low=intraday.low,
                close=intraday.close,
                volume=intraday.volume,
                vwap=intraday.vwap,
            )

        daily = await self._price_repo.get_latest_daily(symbol_row.id)
        if daily is not None:
            return LatestPriceOut(
                symbol=symbol_row.symbol,
                source="daily",
                timestamp=daily.date,
                open=daily.open,
                high=daily.high,
                low=daily.low,
                close=daily.close,
                volume=daily.volume,
                vwap=daily.vwap,
            )

        return None
