# market-intelligence

A production-grade AI Market Scanning Engine for the Indian stock market.

- **Phase 1** shipped the infrastructure: FastAPI app skeleton, async SQLAlchemy +
  Alembic, Docker/Compose deployment, logging, scheduler framework.
- **Phase 2** added the **Market Data Collection Engine**: a provider-abstracted
  pipeline that authenticates against a broker API, downloads symbols/quotes/candles,
  validates them, and stores them in PostgreSQL on a schedule. 5paisa is the first (and
  so far only) provider — swapping in a different broker means writing a new
  `MarketDataProvider` implementation, not touching the collector.
- **Phase 3** added the **Market Feature Engine**: reads validated OHLCV from
  PostgreSQL (never talks to a broker) and computes ~70 reusable technical features
  across 9 categories — trend, momentum, volatility, volume, price action, market
  structure, support/resistance, chart patterns, and relative strength — plus intraday
  session features (opening range, initial balance, day high/low, session VWAP). Written
  once to `daily_features` / `session_features`; scanners read from there and never
  recompute an indicator themselves.
- **Phase 4** (this version) adds the **Scanner Engine v1**: a Strategy-Pattern
  framework (`BaseScanner` + `ScannerRegistry`) with one implementation, the
  **Breakout Scanner**, which qualifies symbols on an aligned EMA stack, ADX trend
  strength, relative volume, and proximity to resistance, then writes a scored
  qualified/rejected verdict to `scanner_results` on a one-minute schedule — reading
  only from `daily_features`, never recomputing an indicator or touching a broker.

AI-driven analysis, Telegram alerting, a dashboard, and order placement are still out of
scope — this is the data + feature + scanning layer everything above it will read from.

## Folder structure

```
market-intelligence/
├── app/
│   ├── api/              FastAPI routers: /health, /market/*, /features/*, /scanner/*
│   ├── config/            Pydantic Settings (env-driven configuration)
│   ├── core/              Cross-cutting concerns: logging, exceptions, middleware
│   ├── data/              collector.py, validator.py, market_updater.py
│   ├── database/          SQLAlchemy async engine, session factory, declarative base
│   ├── features/          Feature engine: engine.py, calculator.py (aggregator),
│   │                      validator.py, indicators.py (shared math), + one calculator.py
│   │                      per category: trend/ momentum/ volatility/ volume/
│   │                      price_action/ structure/ support_resistance/ patterns/
│   │                      relative_strength/ session/
│   ├── models/            symbols, daily_prices, intraday_prices, market_status,
│   │                      collector_logs, daily_features, session_features,
│   │                      scanner_results, scanner_runs, scanner_logs
│   ├── providers/         MarketDataProvider (ABC) + FivePaisaProvider
│   ├── repositories/      All SQLAlchemy queries live here (repository pattern)
│   ├── scanner/           Scanner engine: models.py (domain dataclasses), base_scanner.py
│   │                      (Strategy ABC), breakout_scanner.py, validator.py,
│   │                      scanner_registry.py, scanner_manager.py, engine.py
│   ├── schemas/           Pydantic request/response schemas
│   ├── services/          market_service.py, feature_service.py, scanner_service.py
│   │                      — read-side services
│   ├── alerts/            Not implemented — future phase
│   ├── scheduler/         APScheduler wrapper (service.py) + market data jobs (jobs.py)
│   │                      + feature engine job (feature_jobs.py) + scanner job
│   │                      (scanner_jobs.py)
│   ├── utils/             Shared helpers
│   └── main.py            FastAPI app factory, lifespan, router + job registration
├── alembic/                Migrations (wired to app settings)
├── tests/                  Pytest suite (aiosqlite-backed, no live DB/broker needed)
├── docker/                 Container entrypoint script
├── scripts/                 Operational scripts (e.g. wait_for_db.py)
├── logs/                    Rotating log files (app.log, errors.log)
├── docs/                    Project documentation
├── Dockerfile
├── docker-compose.yml
├── requirements.txt / requirements-dev.txt
├── pyproject.toml           black / ruff / mypy / pytest config
└── .env.example
```

## Tech stack

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy (async) · Alembic · Docker ·
Docker Compose · Redis (optional) · APScheduler · Pydantic Settings · Loguru · Pandas ·
Pytest · Black · Ruff · Mypy

## Market data architecture

```
MarketDataProvider (ABC: connect/disconnect/is_connected/get_symbols/get_quote/get_intraday/get_daily)
        ↑
FivePaisaProvider  (py5paisa SDK, TOTP auth, retry/backoff, rate limiting, reconnect)

MarketDataCollector  →  DataValidator  →  Repositories (symbols/prices/status/logs)  →  PostgreSQL
        ↑
Scheduler jobs (app/scheduler/jobs.py): every-minute intraday (market-hours gated),
daily candles + symbol refresh (cron, outside market hours)
```

- **Provider swap:** add a new class implementing `MarketDataProvider` in `app/providers/`
  and construct it instead of `FivePaisaProvider` in `app/main.py`. Nothing in
  `app/data/` or `app/scheduler/jobs.py` references 5paisa directly.
- **Validation:** rejects negative prices/volume, missing/NaN/inf OHLC, illogical OHLC
  (e.g. high < low), and duplicate timestamps within a batch. See
  [app/data/validator.py](app/data/validator.py).
- **Failure isolation:** one symbol failing to collect doesn't abort the run — the
  collector logs it and continues, recording success/failure counts per run in
  `collector_logs`.
- **Market hours:** NSE regular session, Mon–Fri 09:15–15:30 IST
  (`MarketStatusUpdater.is_market_open`). Does not account for exchange holidays yet.

### 5paisa credentials

Set the `FIVEPAISA_*` variables in `.env` (see [.env.example](.env.example)). Leave them
blank in dev — `Settings.fivepaisa_configured` is `False`, the provider stays
disconnected, and scheduled jobs log a warning and skip themselves instead of crashing.

## Feature engine architecture

```
PostgreSQL (daily_prices / intraday_prices)
        ↓
FeatureEngine.run_daily()    — per symbol: has a newer daily candle arrived than the
        ↓                       last computed feature row? if so, recompute the full
DailyFeatureCalculator          vectorized series over a lookback window (pandas, all
  (9 category calculators)      in memory) and write only the new date(s).
        ↓
FeatureValidator (bounds-clip)
        ↓
daily_features / session_features (PostgreSQL)  →  read by /features/* and, later, scanners
```

- **Never touches a broker.** Only reads OHLCV already validated and stored by the
  Phase 2 collector.
- **Vectorized, stateless recompute.** Each run loads a bounded lookback window
  (`feature_daily_lookback_bars`, default 500 bars) and recomputes the whole series with
  pandas — no incremental/recursive state to get subtly wrong — then only *writes* rows
  newer than what's already stored, so re-running is cheap and duplicate-free.
- **Session features are different in kind.** They're the live state of *today* (opening
  range, day high/low, intraday VWAP), recomputed and overwritten every run rather than
  appended to, unlike the daily categories.
- **Chart patterns are heuristics, not ML.** Triangle/flag/cup-handle/VCP/etc. are
  documented rule-based approximations meant as scanner pre-filters — see the module
  docstring in [app/features/patterns/calculator.py](app/features/patterns/calculator.py).
- **Known gaps, both documented in code:** `rs_vs_nifty` stays `null` until a symbol
  named `NIFTY` (or whatever `FEATURE_RS_BENCHMARK_SYMBOL` is set to) exists in
  `symbols` — the current NSE-equities-only scrip master filter excludes indices.
  `rs_vs_sector` / `sector_rank` stay `null` until `Symbol.sector` is populated from
  some data source (5paisa's scrip master doesn't provide it) — see
  [app/features/relative_strength/calculator.py](app/features/relative_strength/calculator.py).

## Scanner engine architecture

```
daily_features (PostgreSQL, read-only)
        ↓
ScannerRegistry  →  BaseScanner subclasses (Strategy Pattern: validate/scan/score)
        ↓                    ↑
ScannerEngine.run_all()   BreakoutScanner v1 (price>EMA20>EMA50>EMA200, ADX, relative
   opens a scanner_runs      volume, volume increasing, proximity to resistance)
   row per scanner,
   delegates to ↓
ScannerManager  →  per symbol: skip if already scanned today, else validate → scan →
                    score → upsert into scanner_results (dedup on symbol+scanner+date)
```

- **Strategy Pattern.** A new scanner is a `BaseScanner` subclass implementing
  `validate`/`scan`/`score`, registered with `ScannerRegistry` in `app/main.py` — nothing
  in `ScannerManager` or `ScannerEngine` changes. `save_results` (persistence) is shared,
  not reimplemented per scanner. See [app/scanner/base_scanner.py](app/scanner/base_scanner.py).
- **No duplicate alerts.** `scanner_results` is unique on `(symbol_id, scanner_name,
  date)`; `ScannerManager` skips a symbol outright if a result already exists for that
  day, and `ScannerResultRepository.upsert` updates in place if one somehow gets written
  twice.
- **Configurable thresholds, not hardcoded.** ADX/relative-volume/resistance-proximity
  thresholds and the composite score's category weights all live in `Settings`
  (`SCANNER_*` env vars) — see [.env.example](.env.example).
- **Every symbol gets a score, qualified or not.** A rejected symbol's `scanner_results`
  row still carries a 0–100 composite score, so rejects and qualifiers stay comparable.
  A symbol failing `validate()` (missing/out-of-range features) never reaches `scan()` —
  that's logged to `scanner_logs` with no `scanner_results` row at all.
- **Known gap (expected, not a bug):** `ema200` requires 200 days of daily history, and
  most symbols currently have far less backfilled — until that depth exists, the
  Breakout Scanner's `validate()` legitimately rejects most/all symbols for missing
  `ema200`, not because the scanning logic is wrong.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | App-level liveness |
| `GET /market/health` | Market module liveness |
| `GET /market/status` | `market_open`, `provider_connected`, `last_update/success/failure` |
| `GET /market/symbols` | Active symbols from the local symbol master |
| `GET /market/latest/{symbol}` | Latest intraday candle, falling back to latest daily |
| `GET /features/status` | Symbols with features computed, total rows, last run time |
| `GET /features/latest/{symbol}` | Latest daily feature row + today's session features |
| `GET /features/history/{symbol}?limit=100` | Daily feature history, oldest first |
| `GET /scanner/status` | Per-scanner totals: results, qualified count, last run time |
| `GET /scanner/results?scanner_name=&status=&limit=100` | Recent scan results, optionally filtered |
| `GET /scanner/results/{symbol}?limit=50` | Scan result history for one symbol |
| `GET /scanner/runs?scanner_name=&limit=20` | Recent scheduled run summaries |

## Running with Docker (recommended)

1. Copy the environment template and adjust values as needed:

   ```bash
   cp .env.example .env
   ```

2. Build and start the full stack (app + Postgres + Redis):

   ```bash
   docker compose up --build
   ```

3. Confirm the API is up:

   ```bash
   curl http://localhost:8000/health
   # {"status":"ok"}
   curl http://localhost:8000/market/symbols
   # []   (until the symbol-refresh job or a manual collector run populates it)
   ```

4. Stop the stack:

   ```bash
   docker compose down
   ```

   Add `-v` to also drop the Postgres/Redis data volumes.

The app container runs `alembic upgrade head` on startup (see
[docker/entrypoint.sh](docker/entrypoint.sh)) before launching Uvicorn.

## Running locally without Docker

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements-dev.txt
cp .env.example .env           # point POSTGRES_HOST/REDIS_HOST at localhost

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You'll need a local Postgres instance reachable at the host/port in `.env` (Redis is
optional unless `REDIS_ENABLED=true`).

## Database migrations

```bash
# generate a new migration from model changes
alembic revision --autogenerate -m "describe the change"

# apply migrations
alembic upgrade head

# roll back one migration
alembic downgrade -1
```

## Testing & code quality

```bash
pytest                 # run test suite (aiosqlite in-memory DB, fake provider — no network/DB needed)
black app tests        # format
ruff check app tests   # lint
mypy                   # type-check (config in pyproject.toml)
```

## Configuration

All configuration is environment-driven via `app/config/settings.py`
(`pydantic-settings`), sourced from a `.env` file or real environment variables. See
[.env.example](.env.example) for the full list of variables (app, server, database,
Redis, logging, scheduler, 5paisa).

## What's next (out of scope for Phase 4)

Additional scanners (VCP, Momentum, ORB, IPO), Telegram alerting, a dashboard,
AI-driven analysis, and order placement are deliberately not implemented yet — this
phase is the Breakout Scanner v1 only.
