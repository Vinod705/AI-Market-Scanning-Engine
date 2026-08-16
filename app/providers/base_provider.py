"""Market data provider abstraction.

`MarketDataProvider` is the single contract the rest of the application
(collector, scheduler jobs) depends on. Concrete brokers (5paisa today,
others later) implement this interface; nothing outside `app/providers/`
should import a broker SDK directly.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class ProviderError(Exception):
    """Raised for any provider failure: auth, network, timeout, rate limit, bad data."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class ProviderSymbol(BaseModel):
    """A tradable instrument as reported by the provider's symbol master."""

    symbol: str
    exchange: str
    instrument_token: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    listing_date: datetime | None = None
    # ISIN, when the provider's symbol master reports one — used only as a
    # matching key for the IPO listing-date backfill (see
    # scripts/backfill_ipo_listing_dates.py), never persisted on Symbol and
    # never used to infer/fabricate a listing date itself.
    isin: str | None = None


class Quote(BaseModel):
    """A point-in-time snapshot for a symbol."""

    symbol: str
    ltp: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timestamp: datetime


class Candle(BaseModel):
    """A single OHLCV bar, intraday or daily."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None = None


class MarketDataProvider(ABC):
    """Abstract base class every broker/data-vendor integration must implement."""

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate and establish a session. Must be safe to call again to reconnect."""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the session. Safe to call even if not connected."""
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        """Whether a usable, authenticated session currently exists."""
        raise NotImplementedError

    @abstractmethod
    async def get_symbols(self) -> list[ProviderSymbol]:
        """Return the full tradable symbol master from the provider."""
        raise NotImplementedError

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Return the latest quote for `symbol`."""
        raise NotImplementedError

    @abstractmethod
    async def get_intraday(self, symbol: str) -> list[Candle]:
        """Return the latest available intraday candles for `symbol`."""
        raise NotImplementedError

    @abstractmethod
    async def get_daily(self, symbol: str) -> list[Candle]:
        """Return recent daily candles for `symbol`."""
        raise NotImplementedError


class OptionLegSnapshot(BaseModel):
    """One side (call or put) of one strike, as reported live by a
    provider's option-chain endpoint. `close_price`/`prev_oi` are the
    provider's own bundled "previous reading" fields (not derived here) —
    real data the provider returns in the same call, not fabricated."""

    instrument_key: str
    ltp: Decimal
    close_price: Decimal
    volume: int
    oi: Decimal
    prev_oi: Decimal


class OptionChainSnapshot(BaseModel):
    """One strike's full option-chain row (both legs, when the provider
    has data for them)."""

    underlying_symbol: str
    underlying_instrument_key: str
    expiry_date: date
    strike_price: Decimal
    underlying_spot_price: Decimal
    call: OptionLegSnapshot | None = None
    put: OptionLegSnapshot | None = None


class FuturesOiBar(BaseModel):
    """One EOD bar for a single futures contract, including open interest —
    unlike `Candle`, which deliberately has no OI field (see
    `MarketDataProvider`'s callers, none of which need it)."""

    instrument_key: str
    expiry_date: date
    timestamp: datetime
    close: Decimal
    volume: int
    open_interest: int


class DerivativesProvider(ABC):
    """Optional capability: a provider that can supply F&O open-interest
    data implements this alongside `MarketDataProvider`. Not every provider
    needs to (`FivePaisaProvider` does not) — this is an add-on capability,
    not a required one, so callers must check for it rather than assume
    every `MarketDataProvider` has it."""

    @abstractmethod
    async def get_option_chain(
        self, underlying_symbol: str, underlying_instrument_key: str
    ) -> list[OptionChainSnapshot]:
        """Live option-chain snapshot for the underlying's nearest expiry.
        Returns `[]` if the underlying currently has no derivative
        contracts — never fabricated, never a stale/guessed expiry."""
        raise NotImplementedError

    @abstractmethod
    async def get_futures_oi_history(
        self, underlying_symbol: str, lookback_days: int = 5
    ) -> list[FuturesOiBar]:
        """Recent daily bars (oldest first) for the underlying's nearest
        futures contract, including OI. Returns `[]` if the underlying has
        no futures contract."""
        raise NotImplementedError
