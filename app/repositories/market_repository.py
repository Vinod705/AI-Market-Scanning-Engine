"""Repository layer: the only place that issues SQLAlchemy queries for market data.

Services and the collector depend on these repositories rather than the ORM
session directly, keeping persistence concerns out of business logic.
"""

from datetime import date as date_
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collector_log import CollectorLog
from app.models.daily_price import DailyPrice
from app.models.intraday_price import IntradayPrice
from app.models.market_status import SINGLETON_ID, MarketStatus
from app.models.symbol import Symbol
from app.providers.base_provider import Candle, ProviderSymbol


class SymbolRepository:
    """Persistence for the `symbols` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_symbol(self, symbol: str, exchange: str = "N") -> Symbol | None:
        stmt = select(Symbol).where(Symbol.symbol == symbol, Symbol.exchange == exchange)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, symbol_id: int) -> Symbol | None:
        return await self._session.get(Symbol, symbol_id)

    async def list_by_ids(self, symbol_ids: list[int]) -> list[Symbol]:
        if not symbol_ids:
            return []
        stmt = select(Symbol).where(Symbol.id.in_(symbol_ids))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_active(self) -> list[Symbol]:
        stmt = select(Symbol).where(Symbol.is_active.is_(True)).order_by(Symbol.symbol)
        return list((await self._session.execute(stmt)).scalars().all())

    async def upsert(self, provider_symbol: ProviderSymbol) -> Symbol:
        existing = await self.get_by_symbol(provider_symbol.symbol, provider_symbol.exchange)
        if existing is None:
            existing = Symbol(symbol=provider_symbol.symbol, exchange=provider_symbol.exchange)
            self._session.add(existing)

        existing.instrument_token = provider_symbol.instrument_token
        existing.company_name = provider_symbol.company_name
        existing.sector = provider_symbol.sector
        existing.industry = provider_symbol.industry
        existing.listing_date = (
            provider_symbol.listing_date.date() if provider_symbol.listing_date else None
        )
        existing.is_ipo = provider_symbol.is_ipo
        existing.is_active = True

        await self._session.flush()
        return existing

    async def upsert_many(self, provider_symbols: list[ProviderSymbol]) -> int:
        count = 0
        for provider_symbol in provider_symbols:
            await self.upsert(provider_symbol)
            count += 1
        return count


class PriceRepository:
    """Persistence for `daily_prices` and `intraday_prices`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_daily(self, symbol_id: int, candle: Candle) -> None:
        stmt = select(DailyPrice).where(
            DailyPrice.symbol_id == symbol_id, DailyPrice.date == candle.timestamp.date()
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = DailyPrice(symbol_id=symbol_id, date=candle.timestamp.date())
            self._session.add(row)
        row.open, row.high, row.low, row.close = candle.open, candle.high, candle.low, candle.close
        row.volume = candle.volume
        row.vwap = candle.vwap

    async def upsert_daily_many(self, symbol_id: int, candles: list[Candle]) -> int:
        for candle in candles:
            await self.upsert_daily(symbol_id, candle)
        await self._session.flush()
        return len(candles)

    async def upsert_intraday(self, symbol_id: int, candle: Candle) -> None:
        stmt = select(IntradayPrice).where(
            IntradayPrice.symbol_id == symbol_id, IntradayPrice.datetime == candle.timestamp
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = IntradayPrice(symbol_id=symbol_id, datetime=candle.timestamp)
            self._session.add(row)
        row.open, row.high, row.low, row.close = candle.open, candle.high, candle.low, candle.close
        row.volume = candle.volume
        row.vwap = candle.vwap

    async def upsert_intraday_many(self, symbol_id: int, candles: list[Candle]) -> int:
        for candle in candles:
            await self.upsert_intraday(symbol_id, candle)
        await self._session.flush()
        return len(candles)

    async def get_latest_intraday(self, symbol_id: int) -> IntradayPrice | None:
        stmt = (
            select(IntradayPrice)
            .where(IntradayPrice.symbol_id == symbol_id)
            .order_by(IntradayPrice.datetime.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_latest_daily(self, symbol_id: int) -> DailyPrice | None:
        stmt = (
            select(DailyPrice)
            .where(DailyPrice.symbol_id == symbol_id)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_daily_history(self, symbol_id: int, limit: int = 500) -> list[DailyPrice]:
        """Most recent `limit` daily bars for `symbol_id`, oldest first (ready for vectorized calc)."""
        stmt = (
            select(DailyPrice)
            .where(DailyPrice.symbol_id == symbol_id)
            .order_by(DailyPrice.date.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(reversed(rows))

    async def get_intraday_for_date(self, symbol_id: int, day: date_) -> list[IntradayPrice]:
        """All intraday bars for `symbol_id` on `day`, oldest first."""
        stmt = (
            select(IntradayPrice)
            .where(
                IntradayPrice.symbol_id == symbol_id,
                IntradayPrice.datetime >= datetime.combine(day, datetime.min.time()),
                IntradayPrice.datetime < datetime.combine(day, datetime.max.time()),
            )
            .order_by(IntradayPrice.datetime.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())


class MarketStatusRepository:
    """Persistence for the singleton `market_status` row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> MarketStatus | None:
        return await self._session.get(MarketStatus, SINGLETON_ID)

    async def upsert(
        self,
        *,
        market_open: bool | None = None,
        provider_connected: bool | None = None,
        last_update: datetime | None = None,
        last_success: datetime | None = None,
        last_failure: datetime | None = None,
    ) -> MarketStatus:
        row = await self.get()
        if row is None:
            row = MarketStatus(id=SINGLETON_ID, market_open=False, provider_connected=False)
            self._session.add(row)

        if market_open is not None:
            row.market_open = market_open
        if provider_connected is not None:
            row.provider_connected = provider_connected
        if last_update is not None:
            row.last_update = last_update
        if last_success is not None:
            row.last_success = last_success
        if last_failure is not None:
            row.last_failure = last_failure

        await self._session.flush()
        return row


class CollectorLogRepository:
    """Persistence for `collector_logs`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, start_time: datetime) -> CollectorLog:
        log = CollectorLog(
            start_time=start_time, symbols_processed=0, success_count=0, failed_count=0
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def finish(
        self,
        log: CollectorLog,
        *,
        finish_time: datetime,
        symbols_processed: int,
        success_count: int,
        failed_count: int,
        error_message: str | None,
    ) -> None:
        log.finish_time = finish_time
        log.duration = (finish_time - log.start_time).total_seconds()
        log.symbols_processed = symbols_processed
        log.success_count = success_count
        log.failed_count = failed_count
        log.error_message = error_message
        await self._session.flush()
