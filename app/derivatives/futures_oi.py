"""Turns `FuturesOiBar` history (see `app.providers.base_provider`) into one
`OiReading` for the latest bar of a futures contract.

Unlike the option-chain endpoint, `GET /historical-candle/...` doesn't
bundle a "previous reading" in the same response — change/classification
here comes from the two most recent real fetched bars (latest vs. the one
before it), never a single-point guess. If only one bar exists (e.g. a
contract that rolled today), there is no prior reading and the result is
`NEUTRAL`, not fabricated.
"""

from decimal import Decimal

from app.derivatives.derivatives_models import InstrumentType, OiReading
from app.derivatives.oi_buildup import classify, percent_change
from app.providers.base_provider import FuturesOiBar


def reading_from_futures_bars(
    underlying_symbol: str, bars: list[FuturesOiBar]
) -> OiReading | None:
    """`bars` must be oldest-first (see `DerivativesProvider.get_futures_oi_history`).
    Returns `None` if there are no bars at all."""
    if not bars:
        return None
    latest = bars[-1]
    previous = bars[-2] if len(bars) >= 2 else None

    latest_oi = Decimal(latest.open_interest)
    previous_oi = Decimal(previous.open_interest) if previous else None
    price_change = latest.close - previous.close if previous else None
    oi_change = latest_oi - previous_oi if previous_oi is not None else None

    return OiReading(
        underlying_symbol=underlying_symbol,
        instrument_key=latest.instrument_key,
        instrument_type=InstrumentType.FUTURES,
        strike_price=None,
        expiry_date=latest.expiry_date,
        observed_at=latest.timestamp,
        price=latest.close,
        prev_price=previous.close if previous else None,
        price_change_pct=percent_change(latest.close, previous.close if previous else None),
        volume=latest.volume,
        oi=latest_oi,
        prev_oi=previous_oi,
        oi_change=oi_change,
        oi_change_pct=percent_change(latest_oi, previous_oi),
        classification=classify(price_change, oi_change),
    )
