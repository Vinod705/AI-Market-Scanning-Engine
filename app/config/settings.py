"""Application configuration loaded from environment variables."""

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
