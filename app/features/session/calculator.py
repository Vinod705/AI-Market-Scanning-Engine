"""Intraday session features: opening range, initial balance, day high/low, session VWAP.

These operate on today's intraday bars only and produce one row of scalars
(the session's state as of the most recent bar), not a per-bar time series
like the daily categories — session features are continuously overwritten
through the day rather than appended to, so there's nothing to vectorize
across dates the way `calculate(df) -> DataFrame` does elsewhere.
"""

from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from app.features import indicators

_MARKET_OPEN = time(9, 15)  # IST — see calculate()'s tz handling below
_OPENING_RANGE_MINUTES = 15
_INITIAL_BALANCE_MINUTES = 60


@dataclass
class SessionFeatures:
    opening_range_high: Decimal | None
    opening_range_low: Decimal | None
    initial_balance_high: Decimal | None
    initial_balance_low: Decimal | None
    day_high: Decimal | None
    day_low: Decimal | None
    session_vwap: Decimal | None


class SessionFeatureCalculator:
    @staticmethod
    def calculate(intraday_df: pd.DataFrame, market_timezone: str = "Asia/Kolkata") -> SessionFeatures:
        """`intraday_df` must be indexed by datetime, sorted ascending, for one trading day."""
        if intraday_df.empty:
            return SessionFeatures(None, None, None, None, None, None, None)

        # Explicit IST business date, not a bare `.date()` on whatever tz
        # the first bar's index happens to carry — this coincides with
        # `intraday_df.index.tz`'s own calendar date during real NSE
        # market hours (09:15-15:30 IST never crosses a UTC midnight),
        # but doesn't rely on that coincidence for a stray pre/post-market
        # bar (e.g. from a WS reconnect gap-fill).
        first_bar = intraday_df.index[0]
        session_date = (
            first_bar.tz_convert(ZoneInfo(market_timezone)).date()
            if first_bar.tzinfo is not None
            else first_bar.date()
        )
        naive_market_open = pd.Timestamp.combine(session_date, _MARKET_OPEN)
        if intraday_df.index.tz is not None:
            # Bug fix: this used to tz_localize the naive "9:15" directly
            # into intraday_df's own tz (UTC, per IntradayPrice.datetime's
            # storage convention) — treating market open as 09:15 UTC
            # (14:45 IST) instead of the real 09:15 IST (03:45 UTC).
            # Confirmed live: a real session_features row's
            # opening_range_high/low exactly matched day_high/day_low, the
            # signature of "opening range" actually covering the whole
            # available session rather than the true first 15 minutes.
            # _MARKET_OPEN is IST — localize into IST first, then convert
            # into whatever tz the data is actually in, so the boundary
            # means the same real-world instant, not the same clock digits.
            market_open_dt = naive_market_open.tz_localize(ZoneInfo(market_timezone)).tz_convert(
                intraday_df.index.tz
            )
        else:
            # Naive data (e.g. fixtures already expressed in market-local
            # clock time) — nothing to convert, compare naive-to-naive.
            market_open_dt = naive_market_open

        opening_range_end = market_open_dt + timedelta(minutes=_OPENING_RANGE_MINUTES)
        initial_balance_end = market_open_dt + timedelta(minutes=_INITIAL_BALANCE_MINUTES)

        opening_range = intraday_df[intraday_df.index < opening_range_end]
        initial_balance = intraday_df[intraday_df.index < initial_balance_end]

        typical_price = indicators.typical_price(
            intraday_df["high"], intraday_df["low"], intraday_df["close"]
        )
        cumulative_volume = intraday_df["volume"].cumsum()
        cumulative_pv = (typical_price * intraday_df["volume"]).cumsum()
        vwap_series = cumulative_pv / cumulative_volume.replace(0, pd.NA)

        def _decimal(value: float | None) -> Decimal | None:
            return None if value is None or pd.isna(value) else Decimal(str(round(float(value), 4)))

        return SessionFeatures(
            opening_range_high=(
                _decimal(opening_range["high"].max()) if not opening_range.empty else None
            ),
            opening_range_low=(
                _decimal(opening_range["low"].min()) if not opening_range.empty else None
            ),
            initial_balance_high=(
                _decimal(initial_balance["high"].max()) if not initial_balance.empty else None
            ),
            initial_balance_low=(
                _decimal(initial_balance["low"].min()) if not initial_balance.empty else None
            ),
            day_high=_decimal(intraday_df["high"].max()),
            day_low=_decimal(intraday_df["low"].min()),
            session_vwap=_decimal(vwap_series.iloc[-1]) if not vwap_series.empty else None,
        )
