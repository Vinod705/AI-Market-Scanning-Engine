"""Application configuration loaded from environment variables."""

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Central application settings, sourced from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "market-intelligence"
    app_env: str = Field(default="development")
    debug: bool = Field(default=False)
    api_v1_prefix: str = "/api/v1"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Database ---
    postgres_user: str = "market_intel"
    postgres_password: str = "changeme"
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "market_intelligence"

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_echo: bool = False

    # --- Redis (optional) ---
    redis_enabled: bool = False
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Auth / dashboard sessions ---
    # Sessions live in Redis (server-side, revocable — see app/auth/session_store.py),
    # not as self-contained JWTs, so logout/expiry/disable-account are all real.
    session_cookie_name: str = "session_id"
    session_ttl_minutes: int = 480  # 8 hours; sliding — refreshed on each authenticated request
    session_cookie_secure: bool = True  # False only for local plain-HTTP dev

    # --- Logging ---
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    log_rotation: str = "10 MB"
    log_retention: str = "14 days"

    # --- Scheduler ---
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Kolkata"

    # --- Market data ingestion / pipeline (Phase 1) ---
    # Intraday collection is a self-pacing continuous loop, not a fixed
    # APScheduler interval (see app.data.ingestion_worker) — this is its
    # *target* cadence when a pass finishes early, not a hard deadline it
    # can miss. When a pass takes longer (rate-limit bound), the loop just
    # runs the next pass immediately rather than skipping a trigger.
    market_data_ingestion_min_interval_seconds: float = 60.0
    # How often the ingestion loop re-checks "is the market open yet"
    # while it's closed, instead of busy-looping.
    market_closed_poll_interval_seconds: float = 30.0
    # Redis Stream connecting ingestion to the downstream feature/scanner/
    # decision worker (see app.pipeline) — reuses the same Redis instance
    # app.auth.redis_client already requires for dashboard sessions.
    pipeline_stream_key: str = "pipeline:ingestion_events"
    pipeline_consumer_group: str = "pipeline_workers"
    pipeline_consumer_name: str = "pipeline_worker_1"
    # Approximate trim cap (Redis XADD MAXLEN ~) so the stream can't grow
    # unbounded if the worker is ever down for an extended period.
    pipeline_stream_maxlen: int = 500
    # XREADGROUP BLOCK timeout in ms — bounds how long the worker blocks
    # per read so its stop() flag gets checked periodically.
    pipeline_stream_block_ms: int = 5000

    # --- 5paisa provider ---
    fivepaisa_app_name: str = ""
    fivepaisa_app_source: str = ""
    fivepaisa_user_id: str = ""
    fivepaisa_password: str = ""
    fivepaisa_user_key: str = ""
    fivepaisa_encryption_key: str = ""
    fivepaisa_client_code: str = ""
    fivepaisa_pin: str = ""
    fivepaisa_totp_secret: str = ""

    # 30s to comfortably cover get_scrips (~165k-row scrip master CSV download,
    # much larger than a quote/candle call — this is the slowest call by far).
    fivepaisa_request_timeout: float = 30.0
    fivepaisa_max_retries: int = 3
    fivepaisa_retry_backoff_seconds: float = 2.0
    fivepaisa_rate_limit_per_sec: float = 5.0

    # ~400 calendar days comfortably covers 200+ trading days (accounting for
    # weekends/holidays), which downstream features like ema200 require.
    fivepaisa_daily_history_days: int = 400

    # --- Upstox provider (Phase 2 — primary by default, see active_market_data_provider) ---
    # api_key/api_secret/redirect_uri are for a future out-of-band OAuth2
    # authorization-code exchange helper — Upstox's own docs say headless/
    # unattended login isn't supported (browser interaction required), so
    # connect() itself uses a pre-obtained access_token, not these.
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = ""
    upstox_access_token: str = ""
    upstox_base_url: str = "https://api.upstox.com/v2"
    # Public, unauthenticated NSE equities instruments master (see
    # https://upstox.com/developer/api-documentation/instruments/).
    upstox_instruments_url: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    )
    upstox_request_timeout: float = 30.0
    upstox_max_retries: int = 3
    upstox_retry_backoff_seconds: float = 2.0
    # Official "Standard REST APIs" limit (covers historical candles, quotes)
    # per https://upstox.com/developer/api-documentation/rate-limiting/.
    upstox_rate_limit_per_sec: float = 50.0
    upstox_daily_history_days: int = 400

    # --- Upstox WebSocket market feed (Phase 3) ---
    # ltpc-only by design: the LTPC protobuf message has no OI/greeks/depth
    # field at all, which structurally satisfies "no OI yet" — nothing to
    # filter out of a richer payload, nothing to accidentally leak.
    upstox_ws_mode: str = "ltpc"
    upstox_ws_authorize_url: str = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    upstox_ws_ping_interval_seconds: float = 20.0
    upstox_ws_ping_timeout_seconds: float = 20.0
    # No message at all (not just no ticks for one symbol) within this
    # window is treated as a stale connection and forces a reconnect.
    upstox_ws_stale_threshold_seconds: float = 30.0
    upstox_ws_reconnect_backoff_seconds: float = 2.0
    upstox_ws_reconnect_max_backoff_seconds: float = 60.0
    # How often completed 1-minute candles are batch-written to Postgres and
    # a PipelineEvent published — not per-tick, see app.providers.upstox_websocket.
    upstox_ws_flush_interval_seconds: float = 15.0

    # Once the WS feed is live and covering the continuous case,
    # IntradayIngestionWorker's REST sweep only needs to run as an
    # infrequent safety net (missing-data catch-all) rather than every
    # ~60s for every symbol — see app.main's wiring. Used only when Upstox
    # WS is active; IntradayIngestionWorker keeps its original short
    # interval (market_data_ingestion_min_interval_seconds) otherwise.
    market_data_ingestion_ws_backup_interval_seconds: float = 600.0

    # Which MarketDataProvider app.main constructs for the collector/pipeline.
    # Upstox is primary per the Phase 2 architecture; FivePaisa stays fully
    # supported as the legacy/secondary option (also always used for
    # app.scheduler.universe_jobs's F&O-root derivation regardless of this
    # setting — see app.main's wiring comment for why).
    active_market_data_provider: Literal["upstox", "fivepaisa"] = "upstox"

    # --- Feature engine ---
    # rs_vs_nifty stays null until a symbol with this name exists in `symbols`
    # — the 5paisa scrip master (filtered to NSE cash-segment equities) does
    # not currently include index instruments.
    feature_rs_benchmark_symbol: str = "NIFTY"
    feature_daily_lookback_bars: int = 500

    # --- Scanner engine: Breakout Scanner v1 thresholds ---
    scanner_breakout_adx_threshold: float = 20.0
    scanner_breakout_relative_volume_threshold: float = 1.5
    # Separate, deliberately looser than the threshold above: "volume isn't
    # declining" rather than "volume is unusually high".
    scanner_breakout_volume_increasing_min_relative_volume: float = 1.0
    scanner_breakout_resistance_proximity_pct: float = 3.0
    scanner_min_qualifying_score: float = 60.0

    # --- Scanner engine: composite score weights (should sum to 1.0) ---
    scanner_score_weight_trend: float = 0.25
    scanner_score_weight_momentum: float = 0.20
    scanner_score_weight_volume: float = 0.20
    scanner_score_weight_volatility: float = 0.10
    scanner_score_weight_relative_strength: float = 0.10
    scanner_score_weight_support_resistance: float = 0.15

    # --- Decision engine: Decision Rules v1 thresholds ---
    decision_min_alert_score: float = 80.0
    decision_min_rvol: float = 2.0
    decision_min_adx: float = 25.0
    decision_resistance_distance_percent: float = 3.0
    # How old a scanner result's underlying feature date may be before the
    # "market data freshness" rule rejects it as stale.
    decision_max_data_age_days: int = 1

    # --- Alert manager ---
    alert_cooldown_minutes: int = 30
    alert_high_priority_score: float = 90.0
    alert_expiry_minutes: int = 240
    alert_max_retries: int = 3
    alert_retry_delay_seconds: float = 5.0

    # --- Market session (reused by the decision engine's session-validity rule;
    # MarketStatusUpdater.is_market_open() reads these instead of hardcoding NSE hours) ---
    market_timezone: str = "Asia/Kolkata"
    market_open_time: time = time(9, 15)
    market_close_time: time = time(15, 30)

    # --- Fundamental Intelligence Engine ---
    # Category weights for the Fundamental Score (should sum to 1.0). Applied
    # only over categories that actually have data — see
    # app/fundamentals/scorer.py's docstring for why missing data is never
    # treated as zero.
    fundamental_weight_growth: float = 0.20
    fundamental_weight_profitability: float = 0.20
    fundamental_weight_financial_strength: float = 0.20
    fundamental_weight_cash_flow: float = 0.15
    fundamental_weight_valuation: float = 0.15
    fundamental_weight_ownership: float = 0.10
    # Below this data-completeness percentage, the score is reported as
    # LIMITED rather than a bare number — a low-data score must never look
    # as reliable as a fully-supported one.
    fundamental_min_data_completeness_pct: float = 50.0

    # --- Trendlyne MCP (Phase 7 fundamental-data source) ---
    # The full remote MCP server URL, including its auth token as a query
    # parameter (Trendlyne's own "No Auth" scheme — the URL itself is the
    # secret; see app/fundamentals/trendlyne_mcp_client.py). Leave blank in
    # dev — the provider stays disconnected and candidates fall back to
    # UnavailableFundamentalDataProvider (honest UNKNOWN) instead of failing.
    # NEVER log this value.
    trendlyne_mcp_url: str = ""
    trendlyne_mcp_request_timeout: float = 20.0
    # /health must stay fast even when Trendlyne is unreachable — Docker's
    # own container healthcheck only allows 5s (see Dockerfile). This is
    # deliberately much shorter than the real fetch timeout above and used
    # ONLY by TrendlyneMcpClient.health_check(), never by the actual
    # fundamental-data fetch path.
    trendlyne_health_check_timeout_seconds: float = 2.0
    # Fundamentals change far less often than intraday technicals — no need
    # to call Trendlyne on every scan cycle for the same symbol.
    fundamental_cache_ttl_minutes: int = 240

    # --- Fundamental Queue (Phase 7.x) ---
    # A scan cycle can discover 500-700+ candidates; firing a Trendlyne
    # request for every one in a tight loop exhausted the account's rate
    # limit in minutes (see app/fundamentals/queue_service.py's module
    # docstring for the incident). The queue processes candidates in small,
    # paced batches instead — tune these against Trendlyne's actual
    # documented/observed limits, not assumptions.
    fundamental_batch_size: int = 10
    fundamental_batch_delay_seconds: float = 30.0
    fundamental_request_delay_seconds: float = 2.0
    # How long to leave the whole queue paused after Trendlyne reports a
    # rate limit, before trying again — deliberately coarse (not a tight
    # retry loop): the queue checks this once per run, it never polls
    # Trendlyne in a loop to detect when the limit clears.
    fundamental_rate_limit_cooldown_seconds: float = 1800.0
    # Trendlyne's rate-limit error carries no reset-time/Retry-After info
    # (see app/fundamentals/trendlyne_mcp_client.py) — the exact reset
    # window is unknown, so rather than guess it, each consecutive
    # rate-limited attempt (no success in between) waits
    # cooldown * multiplier^n, capped at the max below. This is what stops
    # the queue from retrying every `fundamental_rate_limit_cooldown_seconds`
    # forever while an account-level quota stays exhausted.
    fundamental_rate_limit_backoff_multiplier: float = 2.0
    fundamental_rate_limit_max_cooldown_seconds: float = 21600.0  # 6h ceiling
    # Hard ceilings so a single manual/accidental run can never consume the
    # whole account quota. run cap bounds one run_queue() invocation; day
    # cap bounds total requests across all runs today (tracked in
    # fundamental_fetch_log, so it survives an application restart).
    fundamental_max_requests_per_run: int = 200
    fundamental_max_requests_per_day: int = 350

    # --- Technical Score (0-100, reuses Phase 3 daily/session features; should sum to 1.0) ---
    technical_weight_trend: float = 0.25
    technical_weight_momentum: float = 0.20
    technical_weight_volume: float = 0.20
    technical_weight_volatility: float = 0.10
    technical_weight_vwap: float = 0.15
    technical_weight_structure: float = 0.10

    # --- Overall Setup Score (fundamental+technical -> 0-100; should sum to 1.0).
    # Technical dominates by design: fundamentals provide quality context,
    # not intraday timing — see app/candidates/scoring.py. ---
    overall_fundamental_weight: float = 0.3
    overall_technical_weight: float = 0.7

    # --- F&O Intraday Momentum Scanner v1 ---
    fno_momentum_min_rvol: float = 1.5
    fno_momentum_min_adx: float = 20.0
    fno_momentum_min_score: float = 60.0

    # --- Pre-Breakout Scanner v1 (PRE_BREAKOUT / BREAKOUT_CONFIRMED / MOMENTUM) ---
    # Deliberately wider than breakout_v1's own resistance proximity — this
    # scanner's job is to catch setups *before* they're breakout_v1-ready.
    pre_breakout_proximity_pct: float = 5.0
    pre_breakout_min_score: float = 55.0

    # --- IPO Intraday Scanner v1 ---
    ipo_intraday_min_rvol: float = 1.5
    ipo_intraday_min_score: float = 55.0
    # Price-band filters derived from an explicit 52-week (trailing ~252
    # trading session) High/Low over daily_prices (see
    # PriceRepository.get_52_week_high_low) — NOT resistance_level/
    # swing_high/swing_low (rolling-window technical levels, different
    # purpose) and NOT "since listing": our local daily_prices history is
    # only ~400 days deep, so an unbounded "since listing" high/low would
    # silently just be a ~400-day figure for anything older than that.
    ipo_intraday_max_pct_below_52w_high: float = 10.0
    ipo_intraday_min_pct_above_52w_low: float = 100.0
    ipo_intraday_min_price: float = 25.0
    ipo_intraday_min_volume: int = 250_000

    # --- IPO universe membership (app.universe.provider.UniverseProvider) ---
    # Real listing-date age, not local data availability. Symbol.listing_date
    # must be populated from a verified external source (see
    # scripts/backfill_ipo_listing_dates.py) — never inferred from
    # created_at, first daily_prices bar, or bar count, none of which can
    # reliably express "listed within N years" (our own OHLCV history is
    # only ~400 days deep, far short of a multi-year window).
    ipo_universe_max_age_years: int = 3

    # --- Telegram Bot API notification provider ---
    # This "default" bot predates the IPO/F&O split below and still carries
    # breakout_v1 alerts (which have no IPO/F&O alert_category) — see
    # app/notifications/router.py.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_connect_timeout: float = 10.0
    telegram_request_timeout: float = 15.0
    telegram_max_retries: int = 3
    telegram_retry_backoff_seconds: float = 2.0
    # /health calls getMe on every configured bot (up to 3) sequentially —
    # Docker's own container healthcheck only allows 5s (see Dockerfile).
    # Deliberately much shorter than the real send timeouts above and used
    # ONLY by TelegramProvider.health_check(), never by send_message.
    telegram_health_check_timeout_seconds: float = 2.0

    # --- Dedicated Telegram bots for IPO and F&O candidate alerts ---
    # Separate credentials so an IPO alert can never physically be sent by
    # the F&O bot's token or vice versa (see app/notifications/router.py).
    # Connect/request timeouts and retry/backoff are shared with the
    # default bot above — only the credentials differ.
    ipo_telegram_bot_token: str = ""
    ipo_telegram_chat_id: str = ""
    fno_telegram_bot_token: str = ""
    fno_telegram_chat_id: str = ""

    # --- Compounding / Opportunity Layer (app/compounding/) ---
    # Reuses existing support/resistance/ATR technical levels and the
    # existing Fundamental Score — no new data source. See
    # app/compounding/engine.py for the full calculation approach.
    # Weights below should sum to 1.0.
    compounding_atr_stop_multiplier: float = 1.5
    compounding_weight_upside: float = 0.35
    compounding_weight_risk_reward: float = 0.30
    compounding_weight_higher_timeframe: float = 0.15
    compounding_weight_fundamental: float = 0.20
    compounding_strong_score_threshold: float = 75.0
    compounding_trade_score_threshold: float = 55.0

    @field_validator("log_dir", mode="before")
    @classmethod
    def _coerce_log_dir(cls, value: str | Path) -> Path:
        return Path(value)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL used by Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def fivepaisa_configured(self) -> bool:
        """Whether enough 5paisa credentials are present to attempt a login."""
        return bool(
            self.fivepaisa_app_name
            and self.fivepaisa_user_id
            and self.fivepaisa_password
            and self.fivepaisa_user_key
            and self.fivepaisa_encryption_key
            and self.fivepaisa_client_code
            and self.fivepaisa_pin
            and self.fivepaisa_totp_secret
        )

    @property
    def upstox_configured(self) -> bool:
        """Whether a (pre-obtained) Upstox access token is present."""
        return bool(self.upstox_access_token)

    @property
    def telegram_configured(self) -> bool:
        """Whether enough Telegram Bot API credentials are present to send messages."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def ipo_telegram_configured(self) -> bool:
        return bool(self.ipo_telegram_bot_token and self.ipo_telegram_chat_id)

    @property
    def fno_telegram_configured(self) -> bool:
        return bool(self.fno_telegram_bot_token and self.fno_telegram_chat_id)

    @property
    def trendlyne_mcp_configured(self) -> bool:
        return bool(self.trendlyne_mcp_url)

    @property
    def fivepaisa_cred(self) -> dict[str, str]:
        """Credential dict in the shape py5paisa.FivePaisaClient expects."""
        return {
            "APP_NAME": self.fivepaisa_app_name,
            "APP_SOURCE": self.fivepaisa_app_source,
            "USER_ID": self.fivepaisa_user_id,
            "PASSWORD": self.fivepaisa_password,
            "USER_KEY": self.fivepaisa_user_key,
            "ENCRYPTION_KEY": self.fivepaisa_encryption_key,
        }


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton for process lifetime)."""
    return Settings()
