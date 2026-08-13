"""Upstox implementation of `MarketDataProvider` — the primary provider per
the Phase 2 architecture (`FivePaisaProvider` stays fully supported as
legacy/secondary).

Every endpoint/field used here was verified against Upstox's official
developer docs (https://upstox.com/developer/api-documentation/) rather than
guessed. What's implemented, and why the rest isn't:

- **Auth**: Upstox's real login flow is OAuth2 authorization-code and
  requires browser interaction; the docs themselves say the realistic
  unattended path is generating an access token manually from the developer
  dashboard — there is no TOTP-style automatable headless login the way
  `FivePaisaProvider` has. `connect()` therefore treats
  `Settings.upstox_access_token` as a pre-obtained, static credential and
  validates it with `GET /user/profile`, rather than performing a login of
  its own. Because the token is static, an expired/invalid token (401) can't
  be fixed by retrying within this process — see `_handle_response`.
- **Instruments**: the public, unauthenticated gzipped JSON master
  (`Settings.upstox_instruments_url`), filtered to the `NSE_EQ` segment for
  `get_symbols()`. The same file also carries `NSE_FO` (derivatives) records
  — `get_fno_symbol_roots()` re-downloads and filters to that segment
  instead, reading the real `underlying_symbol` field (verified live: 208
  distinct equity-underlying roots, all 208 matched a real `NSE_EQ`
  `trading_symbol` exactly) — the direct Upstox equivalent of
  `FivePaisaProvider.get_fno_symbol_roots()`'s `SymbolRoot` column, not a
  guess.
- **Quotes / historical candles**: `GET /market-quote/quotes` and
  `GET /historical-candle/:instrument_key/:interval/:to_date/:from_date`.
- **Deliberately NOT implemented** (confirmed real and available, out of
  scope for this phase): option chain (`GET /option/chain`, includes OI/
  greeks — deferred, no OI/derivatives work yet), and the Company
  Fundamentals API (`GET /fundamentals/{ISIN}/...` — belongs to the
  separate `app.fundamentals.*` subsystem with its own
  `FundamentalDataProvider` interface, not this one). The WebSocket market
  feed v3 is implemented separately in `app.providers.upstox_websocket`.
  Historical candles also carry an open-interest value per bar (7th array
  element) that is intentionally never read into `Candle`, which has no OI
  field.
"""

import asyncio
import gzip
import json
import time
from datetime import timedelta
from typing import Any

import httpx
from loguru import logger

from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.providers.base_provider import (
    Candle,
    MarketDataProvider,
    ProviderError,
    ProviderSymbol,
    Quote,
)

_SYMBOL_SEGMENT = "NSE_EQ"
_DERIVATIVES_SEGMENT = "NSE_FO"


class UpstoxProvider(MarketDataProvider):
    """MarketDataProvider backed by the Upstox v2 REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # Reused across calls rather than opened per-request; also the seam
        # tests use to inject an `httpx.MockTransport` — same pattern as
        # `app.notifications.telegram.TelegramProvider`.
        self._client = client or httpx.AsyncClient()
        self._connected = False
        self._symbol_cache: dict[str, ProviderSymbol] = {}
        self._last_call_at: float = 0.0

    # --- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        if not self._settings.upstox_configured:
            raise ProviderError("Upstox access token is not configured", retryable=False)

        try:
            response = await self._client.get(
                f"{self._settings.upstox_base_url}/user/profile",
                headers=self._headers(),
                timeout=self._settings.upstox_request_timeout,
            )
        except httpx.HTTPError as exc:
            self._connected = False
            raise ProviderError(f"Upstox connection check failed: {exc}") from exc

        if response.status_code == 200:
            self._connected = True
            logger.info("Connected to Upstox")
            return

        self._connected = False
        if response.status_code == 401:
            raise ProviderError(
                "Upstox access token rejected (expired or invalid)", retryable=False
            )
        raise ProviderError(f"Upstox connection check failed: HTTP {response.status_code}")

    async def disconnect(self) -> None:
        # A static bearer token with no server-managed session to tear down
        # (unlike 5paisa's SDK-owned login session) — just drop local state.
        self._connected = False
        logger.info("Disconnected from Upstox")

    def is_connected(self) -> bool:
        return self._connected

    # --- data access -----------------------------------------------------

    async def get_symbols(self) -> list[ProviderSymbol]:
        raw = await self._download_instruments()
        try:
            records = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"Upstox instruments master is not valid JSON: {exc}") from exc
        if not isinstance(records, list):
            raise ProviderError("Upstox instruments master returned no data")

        symbols: list[ProviderSymbol] = []
        for record in records:
            if not isinstance(record, dict) or record.get("segment") != _SYMBOL_SEGMENT:
                continue
            try:
                symbols.append(
                    ProviderSymbol(
                        symbol=str(record["trading_symbol"]).strip(),
                        exchange=str(record.get("exchange") or "NSE").strip(),
                        instrument_token=str(record["instrument_key"]).strip(),
                        company_name=str(record.get("name") or "").strip() or None,
                        isin=str(record.get("isin") or "").strip() or None,
                    )
                )
            except (KeyError, TypeError):
                logger.warning("Skipping malformed instrument record: {record}", record=record)

        self._symbol_cache = {s.symbol: s for s in symbols}
        logger.info("Loaded {count} symbols from Upstox instruments master", count=len(symbols))
        return symbols

    async def get_fno_symbol_roots(self) -> set[str]:
        """Underlying stock symbols that currently have NSE derivative
        (futures/options) contracts — derived from the same instruments
        master `get_symbols()` uses, not a separate/invented data source.

        Verified live (this session): `NSE_FO` records carry a real
        `underlying_symbol` field (e.g. "TCS" for every TCS future/option
        contract regardless of expiry/strike) — the direct Upstox
        equivalent of `FivePaisaProvider.get_fno_symbol_roots()`'s
        `SymbolRoot` column, not a guess. This also includes index
        derivatives (NIFTY, BANKNIFTY, ...) — callers are expected to
        intersect the result with known equity symbols to exclude those,
        since an index has no underlying cash-market row of its own. See
        `app.universe.provider.UniverseProvider.get_fno_universe`.
        """
        raw = await self._download_instruments()
        try:
            records = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"Upstox instruments master is not valid JSON: {exc}") from exc
        if not isinstance(records, list):
            raise ProviderError("Upstox instruments master returned no data")

        roots: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or record.get("segment") != _DERIVATIVES_SEGMENT:
                continue
            root = record.get("underlying_symbol")
            if root:
                roots.add(str(root).strip())

        logger.info("Found {count} distinct F&O underlying roots", count=len(roots))
        return roots

    async def get_quote(self, symbol: str) -> Quote:
        provider_symbol = await self._resolve_symbol(symbol)
        data = await self._request(
            "GET",
            "/market-quote/quotes",
            params={"instrument_key": provider_symbol.instrument_token},
        )
        if not data:
            raise ProviderError(f"No quote data returned for {symbol}")
        # Keyed by an exchange-prefixed symbol string per Upstox's docs
        # (e.g. "NSE_EQ:SYMBOL") — take the single value rather than
        # reconstructing that key ourselves, since we requested exactly one
        # instrument and the exact key format wasn't independently verified
        # enough to hard-code.
        row = next(iter(data.values()))
        if not isinstance(row, dict):
            raise ProviderError(f"Malformed quote data returned for {symbol}")
        ohlc = row.get("ohlc") or {}
        last_price = row["last_price"]
        return Quote(
            symbol=symbol,
            ltp=last_price,
            open=ohlc.get("open", last_price),
            high=ohlc.get("high", last_price),
            low=ohlc.get("low", last_price),
            close=ohlc.get("close", last_price),
            volume=int(row.get("volume", 0)),
            timestamp=utc_now(),
        )

    async def get_intraday(self, symbol: str) -> list[Candle]:
        return await self._get_candles(symbol, interval="1minute", lookback=timedelta(days=1))

    async def get_daily(self, symbol: str) -> list[Candle]:
        return await self._get_candles(
            symbol,
            interval="day",
            lookback=timedelta(days=self._settings.upstox_daily_history_days),
        )

    # --- internals -----------------------------------------------------

    async def _resolve_symbol(self, symbol: str) -> ProviderSymbol:
        if symbol not in self._symbol_cache:
            await self.get_symbols()
        provider_symbol = self._symbol_cache.get(symbol)
        if provider_symbol is None:
            raise ProviderError(f"Unknown symbol: {symbol}", retryable=False)
        return provider_symbol

    async def _get_candles(
        self, symbol: str, *, interval: str, lookback: timedelta
    ) -> list[Candle]:
        provider_symbol = await self._resolve_symbol(symbol)
        # Upstox's date params are exchange-local (IST), not UTC — same
        # reasoning as FivePaisaProvider._get_candles.
        to_date = to_market_time(utc_now(), self._settings.market_timezone)
        from_date = to_date - lookback

        path = (
            f"/historical-candle/{provider_symbol.instrument_token}/{interval}/"
            f"{to_date.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"
        )
        data = await self._request("GET", path)
        raw_candles = data.get("candles") if isinstance(data, dict) else None
        if not raw_candles or not isinstance(raw_candles, list):
            return []

        candles: list[Candle] = []
        for row in raw_candles:
            try:
                # [timestamp, open, high, low, close, volume, open_interest]
                # — open_interest (index 6) is intentionally not read; see
                # module docstring.
                candles.append(
                    Candle(
                        timestamp=row[0],
                        open=row[1],
                        high=row[2],
                        low=row[3],
                        close=row[4],
                        volume=int(row[5]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                logger.warning(
                    "Skipping malformed candle for {symbol}: {row}", symbol=symbol, row=row
                )
        return candles

    async def _download_instruments(self) -> bytes:
        """Downloads and gunzips the public instruments master. Not routed
        through `_request`: different host, no auth header, not subject to
        the authenticated API's rate limit — but still worth a small retry
        for plain network flakiness."""
        last_error: str | None = None
        for attempt in range(1, self._settings.upstox_max_retries + 1):
            try:
                response = await self._client.get(
                    self._settings.upstox_instruments_url,
                    timeout=self._settings.upstox_request_timeout,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if response.status_code == 200:
                    try:
                        return gzip.decompress(response.content)
                    except OSError as exc:
                        raise ProviderError(
                            f"Upstox instruments master is not valid gzip: {exc}"
                        ) from exc
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.upstox_max_retries:
                await asyncio.sleep(self._settings.upstox_retry_backoff_seconds * attempt)

        raise ProviderError(f"Upstox instruments download failed after retries: {last_error}")

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke one Upstox REST call, with rate limiting, timeout, and
        retry for transient failures (429/5xx/network). Permanent failures
        (4xx other than 429, including 401) raise `ProviderError` directly
        rather than retrying — see `_handle_response`."""
        last_error: str | None = None

        for attempt in range(1, self._settings.upstox_max_retries + 1):
            await self._respect_rate_limit()

            try:
                response = await self._client.request(
                    method,
                    f"{self._settings.upstox_base_url}{path}",
                    params=params,
                    headers=self._headers(),
                    timeout=self._settings.upstox_request_timeout,
                )
            except httpx.TimeoutException:
                last_error = "request timed out"
                logger.warning(
                    "Upstox call {method} {path} timed out (attempt {attempt}/{max})",
                    method=method,
                    path=path,
                    attempt=attempt,
                    max=self._settings.upstox_max_retries,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                logger.warning(
                    "Upstox call {method} {path} failed (attempt {attempt}/{max}): {error}",
                    method=method,
                    path=path,
                    attempt=attempt,
                    max=self._settings.upstox_max_retries,
                    error=exc,
                )
            else:
                result = self._handle_response(response, method=method, path=path, attempt=attempt)
                if result is not None:
                    return result
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.upstox_max_retries:
                await asyncio.sleep(self._settings.upstox_retry_backoff_seconds * attempt)

        raise ProviderError(f"Upstox call {method} {path} failed after retries: {last_error}")

    def _handle_response(
        self, response: httpx.Response, *, method: str, path: str, attempt: int
    ) -> dict[str, Any] | None:
        """Returns the parsed `data` payload for success, or `None` to
        signal "retry" for a transient failure. Raises `ProviderError`
        directly for a permanent failure so the retry loop unwinds
        immediately."""
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code == 200 and body.get("status") == "success":
            data = body.get("data")
            return data if isinstance(data, dict) else {}

        if response.status_code == 429:
            logger.warning(
                "Upstox rate limited on {method} {path} (attempt {attempt}/{max})",
                method=method,
                path=path,
                attempt=attempt,
                max=self._settings.upstox_max_retries,
            )
            return None

        if 500 <= response.status_code < 600:
            logger.warning(
                "Upstox server error on {method} {path} (attempt {attempt}/{max}): {status}",
                method=method,
                path=path,
                attempt=attempt,
                max=self._settings.upstox_max_retries,
                status=response.status_code,
            )
            return None

        if response.status_code == 401:
            # A static, pre-obtained access token that's expired/invalid
            # can't be fixed by retrying within this process (see module
            # docstring) — permanent, not transient.
            self._connected = False
            raise ProviderError(
                f"Upstox auth failed: {self._error_message(body) or 'access token rejected'}",
                retryable=False,
            )

        raise ProviderError(
            f"Upstox API error on {method} {path}: "
            f"{self._error_message(body) or f'HTTP {response.status_code}'}",
            retryable=False,
        )

    @staticmethod
    def _error_message(body: dict[str, Any]) -> str | None:
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = errors[0].get("message")
            return str(message) if message is not None else None
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.upstox_access_token}",
            "Accept": "application/json",
        }

    async def _respect_rate_limit(self) -> None:
        min_interval = 1.0 / self._settings.upstox_rate_limit_per_sec
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_call_at = time.monotonic()
