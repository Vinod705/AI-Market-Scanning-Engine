# Architecture — Phase 1

## Layers

- **api** — FastAPI routers only. No business logic; delegates to services.
- **services** — business logic, orchestrates database/scanners/indicators (empty in Phase 1).
- **database** — async SQLAlchemy engine/session (connection pool) + declarative `Base`.
  Alembic migrations (`alembic/`) run against a separate sync (`psycopg2`) URL since
  Alembic's autogenerate tooling does not support async engines directly.
- **scheduler** — `SchedulerService` wraps `AsyncIOScheduler`, started/stopped from the
  FastAPI `lifespan` context. No jobs are registered yet; `add_job` is ready for future
  phases to call.
- **core** — logging (Loguru sinks to console + rotating `logs/app.log` /
  `logs/errors.log`), custom `AppError` exception hierarchy, and middleware that tags
  every request with an id, times it, and routes uncaught exceptions to a JSON 500
  response.
- **config** — single `Settings` object (`pydantic-settings`), cached via `lru_cache`,
  sourced from `.env`.

## Request lifecycle

1. `request_context_middleware` assigns a request id and starts a timer.
2. Router handler runs (currently only `GET /health`).
3. Any `AppError` subclass raised is caught by a dedicated handler and mapped to its
   `status_code`; anything else falls through to the catch-all handler (500).
4. Response is logged with method, path, status, and duration.

## Startup/shutdown

`app/main.py::lifespan` — on startup: configure logging, verify DB connectivity
(non-fatal if unreachable, logged as a warning), start the scheduler. On shutdown: stop
the scheduler, dispose the SQLAlchemy engine/connection pool.
