"""Turns a live `OptionChainSnapshot` (see `app.providers.base_provider`)
into `OiReading` rows for both legs (call/put) of a strike.

Change/classification here uses the option-chain snapshot's own bundled
`prev_oi`/`close_price` fields — Upstox's `/option/chain` endpoint already
returns both the current and previous reading in one call (verified live),
so no second fetch or stored history is needed just to know direction.
Persisted rows (see `app.derivatives.oi_engine`) still timestamp every
observation, so a real sequence of stored rows accumulates over repeated
runs for anything that later wants more than one-step history.
"""

from datetime import datetime

from app.core.time import utc_now
from app.derivatives.derivatives_models import InstrumentType, OiReading
from app.derivatives.oi_buildup import classify, percent_change
from app.providers.base_provider import OptionChainSnapshot, OptionLegSnapshot


def _reading(
    row: OptionChainSnapshot,
    instrument_type: InstrumentType,
    leg: OptionLegSnapshot,
    observed_at: datetime,
) -> OiReading:
    price_change = leg.ltp - leg.close_price
    oi_change = leg.oi - leg.prev_oi
    return OiReading(
        underlying_symbol=row.underlying_symbol,
        instrument_key=leg.instrument_key,
        instrument_type=instrument_type,
        strike_price=row.strike_price,
        expiry_date=row.expiry_date,
        observed_at=observed_at,
        price=leg.ltp,
        prev_price=leg.close_price,
        price_change_pct=percent_change(leg.ltp, leg.close_price),
        volume=leg.volume,
        oi=leg.oi,
        prev_oi=leg.prev_oi,
        oi_change=oi_change,
        oi_change_pct=percent_change(leg.oi, leg.prev_oi),
        classification=classify(price_change, oi_change),
    )


def readings_from_chain(snapshot_rows: list[OptionChainSnapshot]) -> list[OiReading]:
    """One `OiReading` per leg (call and/or put) present in each strike row."""
    observed_at = utc_now()
    readings: list[OiReading] = []
    for row in snapshot_rows:
        if row.call is not None:
            readings.append(_reading(row, InstrumentType.CALL, row.call, observed_at))
        if row.put is not None:
            readings.append(_reading(row, InstrumentType.PUT, row.put, observed_at))
    return readings
