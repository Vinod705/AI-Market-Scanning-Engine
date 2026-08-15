"""Repository layer: the only place that issues SQLAlchemy queries for market data.

Services and the collector depend on these repositories rather than the ORM
session directly, keeping persistence concerns out of business logic.
"""

from datetime import date as date_
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collector_log import CollectorLog
from app.models.daily_price import DailyPrice
from app.models.intraday_price import IntradayPrice
from app.models.market_data_feed_log import MarketDataFeedLog
from app.models.market_status import SINGLETON_ID, MarketStatus
from app.models.symbol import Symbol
from app.providers.base_provider import Candle, ProviderSymbol


class SymbolRepository:
    """Persistence for the `symbols` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_symbol(self, symbol: str, exchange: str | None = None) -> Symbol | None:
        """`exchange` is an optional disambiguator, not a required match.

        Bug fix: this used to default to `exchange="N"` and `upsert()`
        always matched on the literal `(symbol, exchange)` pair — safe
        only as long as every provider spelled the exchange the same way.
        Confirmed live this session: FivePaisa writes `"N"`, Upstox writes
        `"NSE"`, so switching primary providers made `upsert()` blind to
        every existing row and insert a duplicate for it instead of
        updating in place (2,465 symbols duplicated in one refresh; see
        the identity-audit findings this data-repair followed). This
        system is NSE-domestic only in practice (confirmed during that
        audit — no real cross-exchange collisions), so matching on
        `symbol` alone is safe and is what every caller that omits
        `exchange` actually wants."""
        stmt = select(Symbol).where(Symbol.symbol == symbol)
        if exchange is not None:
            stmt = stmt.where(Symbol.exchange == exchange)
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
        # Matched by symbol alone, not (symbol, exchange) — see
        # get_by_symbol's docstring for why a provider-specific exchange
        # match broke this across a primary-provider switch.
        existing = await self.get_by_symbol(provider_symbol.symbol)
        if existing is None:
            existing = Symbol(symbol=provider_symbol.symbol, exchange=provider_symbol.exchange)
            self._session.add(existing)

        existing.exchange = provider_symbol.exchange
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

    async def get_latest_daily_bulk(self, symbol_ids: list[int]) -> dict[int, DailyPrice]:
        """Bulk equivalent of `get_latest_daily` — see
        `DailyFeatureRepository.get_latest_bulk`'s docstring for why."""
        return await self.get_daily_bulk_at_rank(symbol_ids, rank=1)

    async def get_daily_bulk_at_rank(
        self, symbol_ids: list[int], *, rank: int
    ) -> dict[int, DailyPrice]:
        """The `rank`-th most recent daily bar (1 = latest, 2 = the one
        before that, ...) for every symbol in one query — generalizes
        `get_latest_daily_bulk` (`rank=1`). Added for market-breadth
        advance/decline calculations (`app.analytics.market.breadth`),
        which need *today's* and *yesterday's* close/volume for the whole
        LISTED universe without an N+1 per-symbol query."""
        if not symbol_ids:
            return {}
        row_number = (
            func.row_number()
            .over(partition_by=DailyPrice.symbol_id, order_by=DailyPrice.date.desc())
            .label("rn")
        )
        ranked = (
            select(DailyPrice.id, row_number).where(DailyPrice.symbol_id.in_(symbol_ids)).subquery()
        )
        stmt = select(DailyPrice).join(ranked, DailyPrice.id == ranked.c.id).where(
            ranked.c.rn == rank
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.symbol_id: row for row in rows}

    async def get_52_week_high_low_bulk(
        self, symbol_ids: list[int]
    ) -> dict[int, tuple[Decimal, Decimal]]:
        """Bulk equivalent of `get_52_week_high_low` — one aggregate query
        for the whole batch instead of one per symbol. Same bounded
        365-day window and same "None if no bars in the window" contract,
        just per-symbol absence instead of a single `None` return."""
        if not symbol_ids:
            return {}
        window_start = date_.today() - timedelta(days=365)
        stmt = (
            select(DailyPrice.symbol_id, func.max(DailyPrice.high), func.min(DailyPrice.low))
            .where(DailyPrice.symbol_id.in_(symbol_ids), DailyPrice.date >= window_start)
            .group_by(DailyPrice.symbol_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {symbol_id: (high, low) for symbol_id, high, low in rows}

    async def list_symbol_ids_with_intraday_on(
        self, symbol_ids: list[int], day: date_
    ) -> set[int]:
        """Bulk equivalent of calling `get_intraday_for_date` per symbol just
        to check "does this symbol have any bars today" — same day-range
        boundaries as `get_intraday_for_date`, but one query for the whole
        batch, returning only symbol_ids (not full rows)."""
        if not symbol_ids:
            return set()
        stmt = (
            select(IntradayPrice.symbol_id)
            .where(
                IntradayPrice.symbol_id.in_(symbol_ids),
                IntradayPrice.datetime >= datetime.combine(day, datetime.min.time()),
                IntradayPrice.datetime < datetime.combine(day, datetime.max.time()),
            )
            .distinct()
        )
        return set((await self._session.execute(stmt)).scalars().all())

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

    async def get_52_week_high_low(self, symbol_id: int) -> tuple[Decimal, Decimal] | None:
        """(highest `high`, lowest `low`) over the trailing 52 weeks (365
        calendar days) of `daily_prices` for `symbol_id`. Deliberately NOT
        "since listing": our local daily_prices history is only ~400 days
        deep (bounded by FivePaisaProvider's fetch window), so an unbounded
        aggregate would silently just be a ~400-day figure for any stock
        older than that — an explicit, bounded 52-week window is honest
        about what this data can actually support. None if the symbol has
        no daily bars in the window."""
        window_start = date_.today() - timedelta(days=365)
        stmt = select(func.max(DailyPrice.high), func.min(DailyPrice.low)).where(
            DailyPrice.symbol_id == symbol_id, DailyPrice.date >= window_start
        )
        high, low = (await self._session.execute(stmt)).one()
        if high is None or low is None:
            return None
        return high, low

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


class MarketDataFeedLogRepository:
    """Persistence for `market_data_feed_logs` — one row per WebSocket
    connect/reconnect cycle (see `app.providers.upstox_websocket`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(self, connected_at: datetime) -> MarketDataFeedLog:
        log = MarketDataFeedLog(connected_at=connected_at)
        self._session.add(log)
        await self._session.flush()
        return log

    async def close(
        self,
        log: MarketDataFeedLog,
        *,
        disconnected_at: datetime,
        messages_received: int,
        ticks_processed: int,
        duplicates_dropped: int,
        candles_flushed: int,
        disconnect_reason: str | None,
    ) -> None:
        log.disconnected_at = disconnected_at
        log.messages_received = messages_received
        log.ticks_processed = ticks_processed
        log.duplicates_dropped = duplicates_dropped
        log.candles_flushed = candles_flushed
        log.disconnect_reason = disconnect_reason
        await self._session.flush()
