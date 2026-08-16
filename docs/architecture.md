# Architecture

> This file previously described a Phase-1 skeleton ("no jobs registered
> yet", `GET /health` as the only route) that no longer matches the
> codebase. **`README.md` is the maintained, accurate architecture
> reference** — folder structure, market data / feature engine / scanner /
> decision & alert engine architecture, API endpoints, auth, Docker, and
> configuration are all documented there. This file is kept only as a short
> pointer + the two structural notes below that don't belong in the README.

See `README.md`, sections:
- Folder structure
- Market data architecture
- Feature engine architecture
- Scanner engine architecture
- Candidate pipeline (F&O / IPO)
- Fundamental analysis
- Decision & Alert architecture
- Operational notes
- API endpoints
- Dashboard, auth, and explainability

## Notes not covered in the README

- **Migrations run against a sync URL.** Alembic's autogenerate tooling does
  not support async engines directly, so `alembic/` runs against a separate
  sync (`psycopg2`) connection string even though the app itself uses an
  async SQLAlchemy engine at runtime.
- **Logging.** `app/core/logging.py` configures three Loguru sinks (stdout,
  rotating `logs/app.log`, `logs/errors.log`). Stdlib `logging` (used by
  APScheduler, Uvicorn) is intercepted and redirected into the same Loguru
  sinks rather than going to stderr separately — see `InterceptHandler` in
  that module.
