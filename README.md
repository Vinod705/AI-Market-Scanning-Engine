# market-intelligence

A production-grade AI Market Scanning Engine for the Indian stock market.

- **Phase 1** shipped the infrastructure: FastAPI app skeleton, async SQLAlchemy +
  Alembic, Docker/Compose deployment, logging, scheduler framework.
- **Phase 2** (this version) adds the **Market Data Collection Engine**: a
  provider-abstracted pipeline that authenticates against a broker API, downloads
  symbols/quotes/candles, validates them, and stores them in PostgreSQL on a schedule.
  5paisa is the first (and so far only) provider — swapping in a different broker means
  writing a new `MarketDataProvider` implementation, not touching the collector.

Scanners, indicators, breakout detection, AI, Telegram, and alerting are still out of
scope — this is the data layer everything else will read from.

## Folder structure

```
market-intelligence/
├── app/
│   ├── api/              FastAPI routers: /health, /market/*
│   ├── config/            Pydantic Settings (env-driven configuration)
│   ├── core/              Cross-cutting concerns: logging, exceptions, middleware
│   ├── data/              collector.py, validator.py, market_updater.py
│   ├── database/          SQLAlchemy async engine, session factory, declarative base
│   ├── models/            symbols, daily_prices, intraday_prices, market_status, collector_logs
│   ├── providers/         MarketDataProvider (ABC) + FivePaisaProvider
│   ├── repositories/      All SQLAlchemy queries live here (repository pattern)
│   ├── schemas/           Pydantic request/response schemas
│   ├── services/          market_service.py — read-side service backing the API
│   ├── scanners/          Not implemented — future phase
│   ├── indicators/        Not implemented — future phase
│   ├── alerts/            Not implemented — future phase
│   ├── scheduler/         APScheduler wrapper (service.py) + market data jobs (jobs.py)
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

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | App-level liveness |
| `GET /market/health` | Market module liveness |
| `GET /market/status` | `market_open`, `provider_connected`, `last_update/success/failure` |
| `GET /market/symbols` | Active symbols from the local symbol master |
| `GET /market/latest/{symbol}` | Latest intraday candle, falling back to latest daily |

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

## What's next (out of scope for Phase 2)

Technical indicators, scanners, breakout detection, AI-driven analysis, Telegram
alerting, and a dashboard are deliberately not implemented yet — this phase is the data
collection layer only.
