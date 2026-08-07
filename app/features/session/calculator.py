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

import pandas as pd

from app.features import indicators

_MARKET_OPEN = time(9, 15)
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
    def calculate(intraday_df: pd.DataFrame) -> SessionFeatures:
        """`intraday_df` must be indexed by datetime, sorted ascending, for one trading day."""
        if intraday_df.empty:
            return SessionFeatures(None, None, None, None, None, None, None)

        session_date = intraday_df.index[0].date()
        market_open_dt = pd.Timestamp.combine(session_date, _MARKET_OPEN)
        if market_open_dt.tzinfo is None and intraday_df.index.tz is not None:
            market_open_dt = market_open_dt.tz_localize(intraday_df.index.tz)

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
