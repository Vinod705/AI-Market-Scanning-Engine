"""Domain types for the derivatives/open-interest layer.

Provider-agnostic: everything here is built from
`app.providers.base_provider`'s raw snapshot types (`OptionChainSnapshot`,
`FuturesOiBar`), never a provider-specific shape leaking through. See
`app.derivatives.option_chain`/`app.derivatives.futures_oi` for the
transforms, and `app.models.oi_observation` for the persisted ORM shape
`OiReading` maps onto.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class InstrumentType(StrEnum):
    FUTURES = "FUT"
    CALL = "CE"
    PUT = "PE"


class BuildupClassification(StrEnum):
    """Standard, industry-wide OI-buildup convention (price direction x OI
    direction) — not a project-specific invention. See
    `app.derivatives.oi_buildup.classify` for the exact rule."""

    LONG_BUILDUP = "LONG_BUILDUP"
    SHORT_BUILDUP = "SHORT_BUILDUP"
    SHORT_COVERING = "SHORT_COVERING"
    LONG_UNWINDING = "LONG_UNWINDING"
    NEUTRAL = "NEUTRAL"


@dataclass
class OiReading:
    """One contract's OI reading at one point in time, plus its change and
    classification versus the immediately-prior reading available for that
    same contract (bundled in the same provider call for options; the
    previous stored/fetched bar for futures — see the two transform
    modules). `strike_price` is `None` for futures, which have no strike."""

    underlying_symbol: str
    instrument_key: str
    instrument_type: InstrumentType
    strike_price: Decimal | None
    expiry_date: date
    observed_at: datetime

    price: Decimal
    prev_price: Decimal | None
    price_change_pct: Decimal | None

    volume: int
    oi: Decimal
    prev_oi: Decimal | None
    oi_change: Decimal | None
    oi_change_pct: Decimal | None

    classification: BuildupClassification
