"""Feature engine: orchestrates price data -> calculators -> feature store.

Never talks to a broker. Reads only validated OHLCV already in PostgreSQL
(written by the Phase 2 collector) and writes to `daily_features` /
`session_features`. Two entry points, run by the scheduler:

- `run_daily()`: once a symbol has a new daily candle, recompute its full
  feature series over a lookback window (vectorized, cheap) and write only
  the rows newer than the last stored feature date (avoids rewriting
  unchanged history every run).
- `run_session()`: every minute during market hours, recompute today's
  session features (opening range, day high/low, intraday VWAP, ...) from
  today's intraday candles.
"""

import math
from dataclasses import dataclass, field
from datetime import date as date_
from decimal import Decimal

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.features.calculator import DailyFeatureCalculator
from app.features.session.calculator import SessionFeatureCalculator
from app.models.daily_price import DailyPrice
from app.models.intraday_price import IntradayPrice
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.worker_heartbeat_repository import WorkerHeartbeatRepository

FEATURE_ENGINE_WORKER_NAME = "feature_engine"

_INTEGER_COLUMNS = {"base_length_days", "sector_rank"}
_BIGINT_COLUMNS = {"volume_ma20"}
_STRING_COLUMNS = {"trend_direction", "break_of_structure"}
_BOOLEAN_COLUMNS = {
    "golden_cross", "death_cross", "atr_expansion", "atr_contraction", "volatility_squeeze",
    "volume_spike", "volume_dry_up", "higher_high", "higher_low", "lower_high", "lower_low",
    "inside_bar", "outside_bar", "gap_up", "gap_down", "nr4", "nr7", "is_range", "is_consolidation",
    "pattern_triangle", "pattern_bull_flag", "pattern_bear_flag", "pattern_flat_base",
    "pattern_ipo_base", "pattern_rectangle", "pattern_cup_handle", "pattern_vcp",
}  # fmt: skip


@dataclass
class FeatureRunResult:
    symbols_processed: int = 0
    symbols_updated: int = 0
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)


def _prices_to_frame(prices: list[DailyPrice]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [float(p.open) for p in prices],
            "high": [float(p.high) for p in prices],
            "low": [float(p.low) for p in prices],
            "close": [float(p.close) for p in prices],
            "volume": [float(p.volume) for p in prices],
        },
        index=pd.DatetimeIndex([p.date for p in prices]),
    )


def _intraday_to_frame(candles: list[IntradayPrice]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [float(c.open) for c in candles],
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
            "volume": [float(c.volume) for c in candles],
        },
        index=pd.DatetimeIndex([c.datetime for c in candles]),
    )


def _clean_value(column: str, value: object) -> object:
    """Convert a pandas/numpy scalar into the Python type the ORM column expects."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):  # catches NaT/np.nan uniformly; raises for some non-scalars
            return None
    except (TypeError, ValueError):
        pass

    if column in _BOOLEAN_COLUMNS:
        return bool(value)
    if column in (_INTEGER_COLUMNS | _BIGINT_COLUMNS):
        return int(value) if isinstance(value, np.floating | float | np.integer | int) else None
    if column in _STRING_COLUMNS:
        return None if value is None else str(value)
    if isinstance(value, np.floating | float | np.integer | int):
        return Decimal(str(round(float(value), 6)))
    return value


def _row_to_values(row: pd.Series) -> dict[str, object]:
    return {column: _clean_value(column, value) for column, value in row.items()}


class FeatureEngine:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._session_factory = session_factory
        self._benchmark_symbol = settings.feature_rs_benchmark_symbol
        self._lookback_bars = settings.feature_daily_lookback_bars
        self._market_timezone = settings.market_timezone

    async def run_daily(self) -> FeatureRunResult:
        result = FeatureRunResult()
        async with self._session_factory() as session:
            symbols = await SymbolRepository(session).list_active()
        result.symbols_processed = len(symbols)

        benchmark_df = await self._load_benchmark()

        # Bug fix: this used to call _run_daily_for_symbol() — which itself
        # re-checks "is this symbol already up to date" — for every active
        # symbol, one DB round trip (or two) at a time. Confirmed live this
        # session: ~9,598 symbols, ~12,000+ queries, ~37s, just to discover
        # nearly all of them had nothing to do. This bulk pre-filter
        # answers the exact same question — latest feature date >= latest
        # price date — in two queries for the whole universe, and is a
        # pure superset-safe filter: any symbol it skips is guaranteed to
        # also return 0 from the unchanged per-symbol logic below, so
        # results are identical, just without the redundant round trips.
        symbol_ids = [s.id for s in symbols]
        async with self._session_factory() as session:
            latest_prices = await PriceRepository(session).get_latest_daily_bulk(symbol_ids)
            latest_features = await DailyFeatureRepository(session).get_latest_bulk(symbol_ids)

        to_process = []
        for symbol in symbols:
            latest_price = latest_prices.get(symbol.id)
            if latest_price is None:
                continue
            latest_feature = latest_features.get(symbol.id)
            if latest_feature is not None and latest_feature.date >= latest_price.date:
                continue
            to_process.append(symbol)

        for symbol in to_process:
            try:
                updated = await self._run_daily_for_symbol(symbol.id, benchmark_df)
                if updated:
                    result.symbols_updated += 1
                    result.rows_written += updated
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception(
                    "Feature calc failed for symbol_id={symbol_id}", symbol_id=symbol.id
                )
                result.errors.append(f"{symbol.symbol}: {exc}")

        logger.info(
            "Daily feature run: {processed} symbols, {updated} updated, {rows} rows written",
            processed=result.symbols_processed,
            updated=result.symbols_updated,
            rows=result.rows_written,
        )
        await self._ping_heartbeat(
            f"{result.symbols_updated} updated, {result.rows_written} rows, "
            f"{len(result.errors)} errors"
        )
        return result

    async def _ping_heartbeat(self, detail: str) -> None:
        """See app.pipeline.worker._ping_heartbeat's docstring — same
        pattern: a pure liveness signal for app.sanity, never allowed to
        affect real feature computation."""
        try:
            async with self._session_factory() as session:
                await WorkerHeartbeatRepository(session).ping_success(
                    FEATURE_ENGINE_WORKER_NAME, detail=detail
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - a heartbeat write must never affect real work
            logger.warning("FeatureEngine: failed to record heartbeat")

    async def run_session(self) -> FeatureRunResult:
        result = FeatureRunResult()
        async with self._session_factory() as session:
            symbols = await SymbolRepository(session).list_active()
        result.symbols_processed = len(symbols)

        # Session features are keyed by the NSE trading day (IST), not the
        # UTC calendar day the underlying instant happens to fall on.
        today = to_market_time(utc_now(), self._market_timezone).date()

        # Bug fix: same throughput issue as run_daily() above —
        # _run_session_for_symbol()'s own "if not candles: return False"
        # early-exit was being reached via a per-symbol query for every
        # active symbol. This bulk-answers "which symbols have any bar
        # today at all" in one query, using the identical day-range
        # boundaries _run_session_for_symbol()'s own query uses, so it's an
        # exact (not approximate) pre-filter — no behavior change, just
        # fewer round trips for the (usually large) majority with nothing
        # to do yet.
        symbol_ids = [s.id for s in symbols]
        async with self._session_factory() as session:
            has_data_today = await PriceRepository(session).list_symbol_ids_with_intraday_on(
                symbol_ids, today
            )
        symbols = [s for s in symbols if s.id in has_data_today]

        for symbol in symbols:
            try:
                updated = await self._run_session_for_symbol(symbol.id, today)
                if updated:
                    result.symbols_updated += 1
                    result.rows_written += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception(
                    "Session feature calc failed for symbol_id={symbol_id}", symbol_id=symbol.id
                )
                result.errors.append(f"{symbol.symbol}: {exc}")

        return result

    # --- internals -----------------------------------------------------

    async def _load_benchmark(self) -> pd.DataFrame | None:
        async with self._session_factory() as session:
            benchmark_symbol = await SymbolRepository(session).get_by_symbol(self._benchmark_symbol)
            if benchmark_symbol is None:
                return None
            history = await PriceRepository(session).get_daily_history(
                benchmark_symbol.id, limit=self._lookback_bars
            )
        return _prices_to_frame(history) if history else None

    async def _run_daily_for_symbol(self, symbol_id: int, benchmark_df: pd.DataFrame | None) -> int:
        async with self._session_factory() as session:
            price_repo = PriceRepository(session)
            feature_repo = DailyFeatureRepository(session)

            latest_price = await price_repo.get_latest_daily(symbol_id)
            if latest_price is None:
                return 0

            latest_feature_date = await feature_repo.get_latest_date(symbol_id)
            if latest_feature_date is not None and latest_feature_date >= latest_price.date:
                return 0  # already up to date — avoid recomputation

            history = await price_repo.get_daily_history(symbol_id, limit=self._lookback_bars)
            if len(history) < 2:
                return 0

            df = _prices_to_frame(history)
            features_df = DailyFeatureCalculator.calculate(df, benchmark_df)

            cutoff = latest_feature_date or date_.min
            new_rows = features_df[features_df.index.date > cutoff]

            written = 0
            for timestamp, row in new_rows.iterrows():
                values = _row_to_values(row)
                await feature_repo.upsert(symbol_id, timestamp.date(), values)
                written += 1

            await session.commit()
            return written

    async def _run_session_for_symbol(self, symbol_id: int, today: date_) -> bool:
        # No "already up to date" short-circuit here (unlike _run_daily_for_symbol):
        # today's intraday bar count keeps growing through the session, and
        # comparing timestamps reliably would mean reasoning about the intraday
        # column's tz-awareness on read-back. A full day's bars (<=~375) is
        # cheap to recompute, so just always do it — correctness over a
        # micro-optimization that isn't needed at this data volume.
        async with self._session_factory() as session:
            price_repo = PriceRepository(session)
            candles = await price_repo.get_intraday_for_date(symbol_id, today)
            if not candles:
                return False

            session_repo = SessionFeatureRepository(session)
            intraday_df = _intraday_to_frame(candles)
            features = SessionFeatureCalculator.calculate(intraday_df, self._market_timezone)

            # Last 2 daily bars, oldest first. If the most recent one is
            # already *today* (the daily collector job ran mid-session),
            # "previous day" is the one before it; otherwise it's simply the
            # most recent daily bar on file.
            recent_daily = await price_repo.get_daily_history(symbol_id, limit=2)
            if recent_daily and recent_daily[-1].date == today:
                prev_day = recent_daily[0] if len(recent_daily) > 1 else None
            else:
                prev_day = recent_daily[-1] if recent_daily else None

            values = {
                "opening_range_high": features.opening_range_high,
                "opening_range_low": features.opening_range_low,
                "initial_balance_high": features.initial_balance_high,
                "initial_balance_low": features.initial_balance_low,
                "day_high": features.day_high,
                "day_low": features.day_low,
                "prev_day_high": prev_day.high if prev_day else None,
                "prev_day_low": prev_day.low if prev_day else None,
                "session_vwap": features.session_vwap,
            }
            await session_repo.upsert(symbol_id, today, values)
            await session.commit()
            return True
