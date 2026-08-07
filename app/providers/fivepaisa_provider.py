"""5paisa implementation of `MarketDataProvider`.

Wraps the official `py5paisa` SDK (synchronous, `httpx`-based) behind the
async provider interface. All blocking SDK/network calls run in a thread
executor via `asyncio.to_thread` so the event loop is never blocked.

Written against and verified live against `py5paisa==0.7.21.2` (pinned in
requirements.txt): `get_totp_session(client_code, totp, pin)` for auth,
`fetch_market_feed(req_list)` for quotes, `historical_data(Exch,
ExchangeSegment, ScripCode, time, From, To)` for candles. Older releases
(pre-0.5) predate TOTP login entirely and only support an email/password/DOB
or OAuth-redirect flow — if downgrading, this provider needs a rewrite, not
just a method rename. `_call_with_retry` still centralizes every SDK
invocation behind `getattr` so a genuinely missing method fails as a clear
`ProviderError` rather than a bare `AttributeError`.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, cast

import pandas as pd
import pyotp
from loguru import logger
from py5paisa import FivePaisaClient

from app.config.settings import Settings
from app.providers.base_provider import (
    Candle,
    MarketDataProvider,
    ProviderError,
    ProviderSymbol,
    Quote,
)

# NSE (Exch="N") cash-segment (ExchType="C") equities (Series="EQ") only.
# The full scrip master also carries BSE/MCX and F&O/currency contracts —
# out of scope for this phase, and mostly expired/inactive instruments.
_SYMBOL_EXCHANGE = "N"
_SYMBOL_SERIES = "EQ"


class FivePaisaProvider(MarketDataProvider):
    """MarketDataProvider backed by the 5paisa broker API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: FivePaisaClient | None = None
        self._connected = False
        self._symbol_cache: dict[str, ProviderSymbol] = {}
        self._last_call_at: float = 0.0

    # --- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        if not self._settings.fivepaisa_configured:
            raise ProviderError("5paisa credentials are not configured", retryable=False)

        def _login() -> bool:
            client = FivePaisaClient(cred=self._settings.fivepaisa_cred)
            totp = pyotp.TOTP(self._settings.fivepaisa_totp_secret).now()
            client.get_totp_session(
                self._settings.fivepaisa_client_code,
                totp,
                self._settings.fivepaisa_pin,
            )
            self._client = client
            return bool(
                getattr(client, "client_code", None) or getattr(client, "Login_check", False)
            )

        try:
            ok = await asyncio.wait_for(
                asyncio.to_thread(_login), timeout=self._settings.fivepaisa_request_timeout
            )
        except TimeoutError as exc:
            self._connected = False
            raise ProviderError("5paisa login timed out") from exc
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure maps to ProviderError
            self._connected = False
            raise ProviderError(f"5paisa login failed: {exc}") from exc

        if not ok:
            self._connected = False
            raise ProviderError("5paisa login rejected (no session established)")

        self._connected = True
        logger.info(
            "Connected to 5paisa as {client_code}", client_code=self._settings.fivepaisa_client_code
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            logout = getattr(self._client, "client_logout", None) or getattr(
                self._client, "logout", None
            )
            if callable(logout):
                try:
                    await asyncio.to_thread(logout)
                except Exception:  # noqa: BLE001 - best-effort logout
                    logger.warning("5paisa logout call failed, continuing shutdown")
        self._client = None
        self._connected = False
        logger.info("Disconnected from 5paisa")

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    # --- data access -----------------------------------------------------

    async def get_symbols(self) -> list[ProviderSymbol]:
        # get_scrips() caches its result on the SDK client (`scrip_data`) for
        # the client's lifetime. Our client is long-lived (one per provider,
        # reused across the daily symbol-refresh job), so clear the cache
        # first — otherwise every run after the first returns the same
        # snapshot forever and new listings/delistings never show up.
        if self._client is not None:
            self._client.scrip_data = None
        df = await self._call_with_retry("get_scrips")
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            raise ProviderError("5paisa scrip master returned no data")

        subset = df[(df["Exch"] == _SYMBOL_EXCHANGE) & (df["Series"] == _SYMBOL_SERIES)]

        symbols: list[ProviderSymbol] = []
        for record in subset.to_dict("records"):
            try:
                symbols.append(
                    ProviderSymbol(
                        symbol=str(record["Name"]).strip(),
                        exchange=str(record["Exch"]).strip(),
                        instrument_token=str(record["ScripCode"]).strip(),
                        company_name=str(record.get("FullName") or "").strip() or None,
                    )
                )
            except (KeyError, TypeError):
                logger.warning("Skipping malformed scrip master record: {record}", record=record)

        self._symbol_cache = {s.symbol: s for s in symbols}
        logger.info("Loaded {count} symbols from 5paisa scrip master", count=len(symbols))
        return symbols

    async def get_quote(self, symbol: str) -> Quote:
        provider_symbol = await self._resolve_symbol(symbol)
        req = [
            {
                "Exch": provider_symbol.exchange,
                "ExchType": "C",
                "ScripCode": int(provider_symbol.instrument_token),
            }
        ]
        payload = await self._call_with_retry("fetch_market_feed", req)
        raw_rows = payload.get("Data") if isinstance(payload, dict) else payload
        if not raw_rows or not isinstance(raw_rows, list):
            raise ProviderError(f"No quote data returned for {symbol}")
        row = cast(dict[str, Any], raw_rows[0])
        return Quote(
            symbol=symbol,
            ltp=row["LastRate"],
            open=row.get("Open", row["LastRate"]),
            high=row.get("High", row["LastRate"]),
            low=row.get("Low", row["LastRate"]),
            close=row.get("PClose", row["LastRate"]),
            volume=int(row.get("TotalQty", 0)),
            timestamp=datetime.now(),
        )

    async def get_intraday(self, symbol: str) -> list[Candle]:
        return await self._get_candles(symbol, timeframe="1m", lookback=timedelta(days=1))

    async def get_daily(self, symbol: str) -> list[Candle]:
        return await self._get_candles(symbol, timeframe="1d", lookback=timedelta(days=100))

    # --- internals -----------------------------------------------------

    async def _resolve_symbol(self, symbol: str) -> ProviderSymbol:
        if symbol not in self._symbol_cache:
            await self.get_symbols()
        provider_symbol = self._symbol_cache.get(symbol)
        if provider_symbol is None:
            raise ProviderError(f"Unknown symbol: {symbol}", retryable=False)
        return provider_symbol

    async def _get_candles(
        self, symbol: str, *, timeframe: str, lookback: timedelta
    ) -> list[Candle]:
        provider_symbol = await self._resolve_symbol(symbol)
        to_date = datetime.now()
        from_date = to_date - lookback

        df = await self._call_with_retry(
            "historical_data",
            provider_symbol.exchange,
            "C",
            int(provider_symbol.instrument_token),
            timeframe,
            from_date.strftime("%Y-%m-%d"),
            to_date.strftime("%Y-%m-%d"),
        )
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []

        candles: list[Candle] = []
        for row in df.to_dict("records"):
            try:
                candles.append(
                    Candle(
                        timestamp=pd.to_datetime(row["Datetime"]),
                        open=row["Open"],
                        high=row["High"],
                        low=row["Low"],
                        close=row["Close"],
                        volume=int(row.get("Volume", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Skipping malformed candle for {symbol}: {row}", symbol=symbol, row=row
                )
        return candles

    async def _call_with_retry(self, method_name: str, *args: object) -> object:
        """Invoke an SDK method by name, with rate limiting, timeout, retry, and reconnect."""
        last_error: Exception | None = None

        for attempt in range(1, self._settings.fivepaisa_max_retries + 1):
            if not self.is_connected():
                await self.connect()

            await self._respect_rate_limit()

            method = getattr(self._client, method_name, None)
            if method is None:
                raise ProviderError(
                    f"Installed py5paisa client has no method '{method_name}' — "
                    "verify the SDK version this provider was written against",
                    retryable=False,
                )

            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(method, *args),
                    timeout=self._settings.fivepaisa_request_timeout,
                )
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "5paisa call {method} timed out (attempt {attempt}/{max})",
                    method=method_name,
                    attempt=attempt,
                    max=self._settings.fivepaisa_max_retries,
                )
            except Exception as exc:  # noqa: BLE001 - any SDK/network failure triggers retry
                last_error = exc
                self._connected = False
                logger.warning(
                    "5paisa call {method} failed (attempt {attempt}/{max}): {error}",
                    method=method_name,
                    attempt=attempt,
                    max=self._settings.fivepaisa_max_retries,
                    error=exc,
                )

            if attempt < self._settings.fivepaisa_max_retries:
                await asyncio.sleep(self._settings.fivepaisa_retry_backoff_seconds * attempt)

        raise ProviderError(
            f"5paisa call {method_name} failed after retries: {last_error}"
        ) from last_error

    async def _respect_rate_limit(self) -> None:
        min_interval = 1.0 / self._settings.fivepaisa_rate_limit_per_sec
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_call_at = time.monotonic()
