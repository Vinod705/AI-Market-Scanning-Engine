"""Market data provider abstraction.

`MarketDataProvider` is the single contract the rest of the application
(collector, scheduler jobs) depends on. Concrete brokers (5paisa today,
others later) implement this interface; nothing outside `app/providers/`
should import a broker SDK directly.
"""

from abc import ABC, abstractmethod
from datetime import datetime
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
    is_ipo: bool = False
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
