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
  validates it with `GET /v3/feed/market-data-feed/authorize` (not
  `/user/profile` — Account/Profile APIs require a static IP on the
  Upstox account, confirmed live with a genuinely valid Analytics Token;
  the authorize endpoint needs only a valid bearer token), rather than
  performing a login of its own. Because the token is static, an
  expired/invalid token (401) can't be fixed by retrying within this
  process — see `_handle_response`.
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
- **Open interest / derivatives** (`DerivativesProvider`, see
  `app.derivatives`): `GET /option/chain` (per-strike OI/`prev_oi`, verified
  live) and `GET /historical-candle/...` against an `NSE_FO` contract's own
  `instrument_key` (OI at bar index 6, verified live) — see
  `get_option_chain`/`get_futures_oi_history` below. Options/futures
  contract-level records (`strike_price`, `expiry`, `instrument_type`,
  `instrument_key`) come from the same instruments master
  `get_fno_symbol_roots()` already reads, just not previously parsed past
  `underlying_symbol`.
- **Deliberately NOT implemented** (confirmed real and available, out of
  scope so far): real-time/intraday OI (the WebSocket v3 feed in
  `app.providers.upstox_websocket` deliberately subscribes `ltpc` mode
  only, which has no OI field by design — `full`/`option_greeks` modes
  exist and do carry it per the fetched `.proto` schema, but switching
  modes would change the live equity tick path this session already
  verified extensively; not touched here), and the Company Fundamentals
  API (`GET /fundamentals/{ISIN}/...` — belongs to the separate
  `app.fundamentals.*` subsystem with its own `FundamentalDataProvider`
  interface, not this one). Historical candles used for *equities*
  (`get_daily`/`get_intraday`) still never read the OI array element —
  equities have no OI concept; only `get_futures_oi_history` reads it, and
  only for genuine F&O contract `instrument_key`s.
"""

import asyncio
import gzip
import json
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.providers.base_provider import (
    Candle,
    DerivativesProvider,
    FuturesOiBar,
    MarketDataProvider,
    OptionChainSnapshot,
    OptionLegSnapshot,
    ProviderError,
    ProviderSymbol,
    Quote,
)

_SYMBOL_SEGMENT = "NSE_EQ"
_DERIVATIVES_SEGMENT = "NSE_FO"


class UpstoxProvider(MarketDataProvider, DerivativesProvider):
    """MarketDataProvider backed by the Upstox v2 REST API. Also implements
    `DerivativesProvider` — verified live this session: the same
    instruments master's `NSE_FO` segment carries real per-contract
    `strike_price`/`expiry`/`instrument_type`/`instrument_key` fields (not
    just the `underlying_symbol` root `get_fno_symbol_roots()` already
    used), `GET /option/chain` returns real `oi`/`prev_oi` per strike/leg,
    and `GET /historical-candle/...` returns real open interest at index 6
    of each bar for a futures contract's own `instrument_key` — see
    `app.derivatives` for what's built on top of this."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # Reused across calls rather than opened per-request; also the seam
        # tests use to inject an `httpx.MockTransport` — same pattern as
        # `app.notifications.telegram.TelegramProvider`.
        self._client = client or httpx.AsyncClient()
        self._connected = False
        self._symbol_cache: dict[str, ProviderSymbol] = {}
        self._last_call_at: float = 0.0
        # Same instruments-master file `get_symbols()` caches for equities
        # (`_symbol_cache`) also carries every NSE_FO record — cached here
        # too so a run touching many underlyings (`OiEngine` over the full
        # F&O universe) downloads+decompresses it once, not once per
        # symbol per call (`get_option_chain` + `get_futures_oi_history`
        # each need it). Cleared only by constructing a new provider —
        # matches `_symbol_cache`'s existing per-instance lifetime, fine
        # for a run-scoped engine like `OiEngine`.
        self._fno_records_cache: list[dict[str, Any]] | None = None

    # --- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        if not self._settings.upstox_configured:
            raise ProviderError("Upstox access token is not configured", retryable=False)

        # Not /user/profile: Account/Profile APIs require a static IP on
        # file with Upstox (confirmed live this session — UDAPI1221 on a
        # genuinely valid Analytics Token), which this local dev
        # environment doesn't have. The v3 market-data-feed authorize
        # endpoint needs no extra params, requires only a valid bearer
        # token, and is confirmed live-permitted for the Analytics Token —
        # a real market-data connectivity check instead of an
        # account-metadata one this token category can never pass here.
        try:
            response = await self._client.get(
                self._settings.upstox_ws_authorize_url,
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

    # --- DerivativesProvider ---------------------------------------------

    async def get_option_chain(
        self, underlying_symbol: str, underlying_instrument_key: str
    ) -> list[OptionChainSnapshot]:
        expiry = await self._nearest_fno_expiry(underlying_symbol)
        if expiry is None:
            return []

        data = await self._request_list(
            "GET",
            "/option/chain",
            params={"instrument_key": underlying_instrument_key, "expiry_date": expiry.isoformat()},
        )

        snapshots: list[OptionChainSnapshot] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                snapshots.append(
                    OptionChainSnapshot(
                        underlying_symbol=underlying_symbol,
                        underlying_instrument_key=underlying_instrument_key,
                        expiry_date=expiry,
                        strike_price=row["strike_price"],
                        underlying_spot_price=row["underlying_spot_price"],
                        call=self._parse_option_leg(row.get("call_options")),
                        put=self._parse_option_leg(row.get("put_options")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Skipping malformed option-chain row for {symbol}: {row}",
                    symbol=underlying_symbol,
                    row=row,
                )
        return snapshots

    async def get_futures_oi_history(
        self, underlying_symbol: str, lookback_days: int = 5
    ) -> list[FuturesOiBar]:
        contract = await self._nearest_fno_contract(underlying_symbol, instrument_type="FUT")
        if contract is None:
            return []
        instrument_key = str(contract["instrument_key"])
        expiry = self._expiry_date(contract["expiry"])

        to_date = to_market_time(utc_now(), self._settings.market_timezone)
        from_date = to_date - timedelta(days=lookback_days)
        path = (
            f"/historical-candle/{instrument_key}/day/"
            f"{to_date.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"
        )
        data = await self._request("GET", path)
        raw_candles = data.get("candles") if isinstance(data, dict) else None
        if not raw_candles or not isinstance(raw_candles, list):
            return []

        bars: list[FuturesOiBar] = []
        for row in raw_candles:
            try:
                # [timestamp, open, high, low, close, volume, open_interest]
                # — same array shape `_get_candles` reads, but here OI
                # (index 6) is the whole point, not skipped.
                bars.append(
                    FuturesOiBar(
                        instrument_key=instrument_key,
                        expiry_date=expiry,
                        timestamp=row[0],
                        close=row[4],
                        volume=int(row[5]),
                        open_interest=int(row[6]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                logger.warning(
                    "Skipping malformed futures OI bar for {symbol}: {row}",
                    symbol=underlying_symbol,
                    row=row,
                )
        bars.sort(key=lambda bar: bar.timestamp)
        return bars

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

    async def _all_fno_records(self) -> list[dict[str, Any]]:
        """Every `NSE_FO` record across the whole instruments master,
        downloaded+parsed once per provider instance and cached — a run
        touching many underlyings (`OiEngine` over the full F&O universe)
        must not re-download the same multi-MB file per symbol per call."""
        if self._fno_records_cache is not None:
            return self._fno_records_cache
        raw = await self._download_instruments()
        try:
            records = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"Upstox instruments master is not valid JSON: {exc}") from exc
        if not isinstance(records, list):
            raise ProviderError("Upstox instruments master returned no data")
        self._fno_records_cache = [
            record
            for record in records
            if isinstance(record, dict) and record.get("segment") == _DERIVATIVES_SEGMENT
        ]
        return self._fno_records_cache

    async def _fno_records(self, underlying_symbol: str) -> list[dict[str, Any]]:
        """Every `NSE_FO` instrument record for one underlying, from the
        same instruments master `get_symbols()`/`get_fno_symbol_roots()`
        use — no separate/invented data source."""
        return [
            record
            for record in await self._all_fno_records()
            if record.get("underlying_symbol") == underlying_symbol
        ]

    async def _nearest_fno_contract(
        self, underlying_symbol: str, *, instrument_type: str
    ) -> dict[str, Any] | None:
        """The not-yet-expired contract of `instrument_type` (e.g. "FUT")
        with the soonest expiry for `underlying_symbol`, or `None` if the
        underlying has none — never guessed, never the furthest/first
        record encountered."""
        now_ms = int(utc_now().timestamp() * 1000)
        candidates = [
            record
            for record in await self._fno_records(underlying_symbol)
            if record.get("instrument_type") == instrument_type
            and isinstance(record.get("expiry"), int | float)
            and int(record["expiry"]) >= now_ms
            and "instrument_key" in record
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda record: record["expiry"])

    async def _nearest_fno_expiry(self, underlying_symbol: str) -> date | None:
        """Soonest not-yet-expired expiry date across every contract type
        (futures/calls/puts) for `underlying_symbol` — all contract types
        for one underlying share the same expiry cycle, so any one of them
        answers "what's the nearest expiry" for the option-chain call."""
        now_ms = int(utc_now().timestamp() * 1000)
        expiries = {
            int(record["expiry"])
            for record in await self._fno_records(underlying_symbol)
            if isinstance(record.get("expiry"), int | float) and int(record["expiry"]) >= now_ms
        }
        if not expiries:
            return None
        return self._expiry_date(min(expiries))

    @staticmethod
    def _expiry_date(expiry_ms: int) -> date:
        """Upstox reports `expiry` as epoch milliseconds (UTC) — see the
        real `NSE_FO` records verified live this session. Reviewed for
        the IST-business-date audit: this is a *fixed exchange fact*
        (the contract's own expiry date, e.g. "last Thursday of the
        month"), not a "what is today" computation, and the epoch
        encodes UTC midnight of that date — `.date()` on it is correct
        as-is; converting through IST would only add 5.5 hours within
        the same calendar day, never changing the result."""
        return datetime.fromtimestamp(expiry_ms / 1000, tz=UTC).date()

    @staticmethod
    def _parse_option_leg(raw: Any) -> OptionLegSnapshot | None:
        if not isinstance(raw, dict):
            return None
        market_data = raw.get("market_data")
        if not isinstance(market_data, dict):
            return None
        instrument_key = raw.get("instrument_key")
        if not instrument_key:
            return None
        try:
            return OptionLegSnapshot(
                instrument_key=str(instrument_key),
                ltp=market_data["ltp"],
                close_price=market_data["close_price"],
                volume=int(market_data.get("volume", 0)),
                oi=market_data["oi"],
                prev_oi=market_data["prev_oi"],
            )
        except (KeyError, TypeError, ValueError):
            return None

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

    async def _request_list(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> list[Any]:
        """Same retry/rate-limit/error contract as `_request`, for the one
        endpoint (`/option/chain`) whose `data` is a JSON array rather than
        an object — `_request`/`_handle_response` intentionally coerce
        non-dict `data` to `{}`, so that pair can't be reused unchanged
        here without breaking their existing dict contract for every other
        caller (quotes, candles)."""
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
                result = self._handle_response_list(
                    response, method=method, path=path, attempt=attempt
                )
                if result is not None:
                    return result
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.upstox_max_retries:
                await asyncio.sleep(self._settings.upstox_retry_backoff_seconds * attempt)

        raise ProviderError(f"Upstox call {method} {path} failed after retries: {last_error}")

    def _handle_response_list(
        self, response: httpx.Response, *, method: str, path: str, attempt: int
    ) -> list[Any] | None:
        """List-returning counterpart to `_handle_response` — same
        status-code contract (429/5xx retry, 401/other 4xx permanent
        failure), only the success-shape check differs (`data` must be a
        list, not a dict)."""
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code == 200 and body.get("status") == "success":
            data = body.get("data")
            return data if isinstance(data, list) else []

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
