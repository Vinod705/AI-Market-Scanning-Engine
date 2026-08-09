"""Application configuration loaded from environment variables."""

from datetime import time
from functools import lru_cache
from pathlib import Path

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

    # --- Dedicated Telegram bots for IPO and F&O candidate alerts ---
    # Separate credentials so an IPO alert can never physically be sent by
    # the F&O bot's token or vice versa (see app/notifications/router.py).
    # Connect/request timeouts and retry/backoff are shared with the
    # default bot above — only the credentials differ.
    ipo_telegram_bot_token: str = ""
    ipo_telegram_chat_id: str = ""
    fno_telegram_bot_token: str = ""
    fno_telegram_chat_id: str = ""

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
