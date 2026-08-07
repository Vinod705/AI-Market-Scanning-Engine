# market-intelligence

Phase 1 foundation for a production-grade AI Market Scanning Engine for the Indian stock
market. This phase ships **infrastructure only**: no market data, scanners, indicators,
alerts, or AI — just the app skeleton this will all be built on top of.

## Folder structure

```
market-intelligence/
├── app/
│   ├── api/            FastAPI routers (currently: /health)
│   ├── config/          Pydantic Settings (env-driven configuration)
│   ├── core/            Cross-cutting concerns: logging, exceptions, middleware
│   ├── database/        SQLAlchemy async engine, session factory, declarative base
│   ├── models/          SQLAlchemy ORM models (empty in Phase 1)
│   ├── schemas/         Pydantic request/response schemas
│   ├── services/        Business logic services (empty in Phase 1)
│   ├── scanners/        Market scanners (not implemented — Phase 2+)
│   ├── indicators/       Technical indicators (not implemented — Phase 2+)
│   ├── alerts/           Alerting/notification logic (not implemented — Phase 2+)
│   ├── scheduler/       APScheduler wrapper (framework only, no jobs registered)
│   ├── utils/            Shared helpers
│   └── main.py           FastAPI app factory, lifespan, router registration
├── alembic/               Migration environment (wired to app settings)
├── tests/                 Pytest suite
├── docker/                Container entrypoint script
├── scripts/               Operational scripts (e.g. wait_for_db.py)
├── logs/                  Rotating log files (app.log, errors.log)
├── docs/                  Project documentation
├── Dockerfile
├── docker-compose.yml
├── requirements.txt / requirements-dev.txt
├── pyproject.toml         black / ruff / mypy / pytest config
└── .env.example
```

## Tech stack

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy (async) · Alembic · Docker ·
Docker Compose · Redis (optional) · APScheduler · Pydantic Settings · Loguru ·
Pytest · Black · Ruff · Mypy

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
   ```

4. Stop the stack:

   ```bash
   docker compose down
   ```

   Add `-v` to also drop the Postgres/Redis data volumes.

The app container runs `alembic upgrade head` on startup (see
[docker/entrypoint.sh](docker/entrypoint.sh)) before launching Uvicorn — safe to run even
with zero migrations, as in Phase 1.

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
optional in Phase 1 unless `REDIS_ENABLED=true`).

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
pytest                 # run test suite
black app tests        # format
ruff check app tests   # lint
mypy                   # type-check (config in pyproject.toml)
```

## Configuration

All configuration is environment-driven via `app/config/settings.py`
(`pydantic-settings`), sourced from a `.env` file or real environment variables. See
[.env.example](.env.example) for the full list of variables (app, server, database,
Redis, logging, scheduler).

## What's next (out of scope for Phase 1)

Market data ingestion, scanners, technical indicators, alerting (including Telegram),
and AI-driven analysis are deliberately not implemented yet — this phase is
infrastructure only.
