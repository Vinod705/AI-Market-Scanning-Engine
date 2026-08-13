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
- **Phase 4** added the **Scanner Engine v1**: a Strategy-Pattern
  framework (`BaseScanner` + `ScannerRegistry`) with one implementation, the
  **Breakout Scanner**, which qualifies symbols on an aligned EMA stack, ADX trend
  strength, relative volume, and proximity to resistance, then writes a scored
  qualified/rejected verdict to `scanner_results` on a one-minute schedule — reading
  only from `daily_features`, never recomputing an indicator or touching a broker.
- **Phase 5** (this version) adds the **Decision & Alert Engine v1**: a second,
  independent rule layer (`DecisionEvaluator`) that re-validates qualified scanner
  candidates against its own configurable thresholds (minimum score, trend, relative
  volume, ADX, resistance proximity, market-session validity, data freshness) and
  classifies each as ALERT / WATCH / REJECT. ALERT-grade decisions flow through
  `AlertManager` (fingerprint-based dedup, configurable cooldown, PostgreSQL
  persistence) into an in-memory `AlertQueue`, decoupling alert creation from
  delivery so a slow/unavailable notification provider never blocks the scanner.
  A `NotificationManager` worker consumes the queue and sends via a
  `NotificationProvider` abstraction — `TelegramProvider` (official Telegram Bot
  API) is the current implementation — with retry/backoff, full delivery-status
  tracking, and restart recovery (pending/retrying alerts reload from PostgreSQL
  rather than being resent or lost). A `NotificationRouter` picks one of three
  independently-configured Telegram bots (default/IPO/F&O) per alert.

AI-driven analysis, a dashboard, and order placement are still out of scope — this is
the data + feature + scanning + decision/alert layer everything above it will read from.

## Folder structure

```
market-intelligence/
├── app/
│   ├── api/              FastAPI routers: /health, /market/*, /features/*, /scanner/*,
│   │                      /alerts/*, /decisions/*
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
│   │                      scanner_results, scanner_runs, scanner_logs,
│   │                      alerts, alert_events, alert_delivery_logs
│   ├── providers/         MarketDataProvider (ABC) + FivePaisaProvider
│   ├── repositories/      All SQLAlchemy queries live here (repository pattern)
│   ├── scanner/           Scanner engine: models.py (domain dataclasses), base_scanner.py
│   │                      (Strategy ABC), breakout_scanner.py, validator.py,
│   │                      scanner_registry.py, scanner_manager.py, engine.py
│   ├── decision/          Decision engine: models.py (Decision/Quality/RuleResult),
│   │                      rules.py (Decision Rules v1), validator.py, evaluator.py, engine.py
│   ├── alerts/            Alert layer: base.py, deduplicator.py (fingerprinting),
│   │                      throttler.py (cooldown), formatter.py (notification message text),
│   │                      queue.py (AlertQueue), manager.py (AlertManager)
│   ├── notifications/     NotificationProvider (ABC) + TelegramProvider + NotificationRouter
│   │                      (IPO/F&O/default bot selection) + NotificationManager
│   │                      (the queue consumer/delivery worker)
│   ├── schemas/           Pydantic request/response schemas
│   ├── services/          market_service.py, feature_service.py, scanner_service.py,
│   │                      alert_service.py, decision_service.py — read-side services
│   ├── scheduler/         APScheduler wrapper (service.py) + market data jobs (jobs.py)
│   │                      + feature engine job (feature_jobs.py) + scanner job
│   │                      (scanner_jobs.py) + decision/alert-expiry jobs (alert_jobs.py)
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

## Decision & Alert engine architecture

```
scanner_results (status="qualified")
        ↓
DecisionEngine.run_all()  →  DecisionEvaluator  →  Decision Rules v1 (app/decision/rules.py)
   reads qualified            re-validates score/trend/RVOL/ADX/resistance
   candidates every              proximity/market-session/data-freshness against
   minute                        Settings' own DECISION_*/MARKET_* thresholds
        ↓
ALERT-grade DecisionResult
        ↓
AlertManager  →  AlertDeduplicator (fingerprint) + AlertThrottler (cooldown)
                  → alerts (PostgreSQL, status=PENDING) → AlertQueue (in-memory)
        ↓
NotificationManager (queue consumer, runs forever)
        ↓
NotificationRouter.resolve(alert_category)
   ├── IPO_PRE_BREAKOUT / IPO_BREAKOUT / IPO_MOMENTUM   →  TelegramProvider (IPO bot)
   ├── FNO_PRE_BREAKOUT / FNO_BREAKOUT / FNO_MOMENTUM   →  TelegramProvider (F&O bot)
   └── anything else (e.g. breakout_v1, no alert_category)  →  TelegramProvider (default bot)
        ↓
alert_delivery_logs + alert_events (full audit trail) + Alert.status (SENT/FAILED/RETRYING)
```

- **A second, independent rule layer.** The Decision Engine doesn't trust the scanner's
  own qualified/rejected verdict — it re-reads the scanner result's stored
  `feature_snapshot` and re-checks trend/RVOL/ADX/resistance itself against its *own*
  (typically stricter) `DECISION_*` thresholds, plus criteria the scanner doesn't apply
  at all: minimum score, market-session validity, and data freshness. This is what
  makes the engine reusable for future scanners without rewriting it — see
  [app/decision/evaluator.py](app/decision/evaluator.py).
- **ALERT / WATCH / REJECT, not just qualified/rejected.** Missing/stale data is an
  outright REJECT. Otherwise, every core rule (score, trend, RVOL, ADX, resistance) must
  pass *and* the market must be open for ALERT; anything short of that — including a
  fully-qualifying signal outside market hours — is WATCH instead of being silently
  dropped. See [app/decision/models.py](app/decision/models.py) for the full
  `RuleResult`/`DecisionResult` shape, and `GET /decisions/{symbol}` to see the live
  rule-by-rule breakdown for any symbol.
- **Score is a score, not a probability.** Alert messages and API responses always
  render `Score: X/100` and `Quality: HIGH/MEDIUM/LOW` — never a claimed probability of
  breakout success, per the spec's explicit requirement.
- **No duplicate alerts, two different ways.** `AlertDeduplicator` builds a deterministic
  fingerprint from (symbol, scanner, signal type, breakout level, signal date) — the same
  setup re-scanned every minute hashes identically and is suppressed once an alert for it
  exists (unless that alert has EXPIRED). `AlertThrottler` separately enforces
  `ALERT_COOLDOWN_MINUTES` per (symbol, signal type), so even a *new* fingerprint can't
  re-notify too soon after the last one. Both suppressions are logged as `SUPPRESSED`
  `alert_events` against the blocking alert.
- **The notification provider is a swappable abstraction.** `AlertManager` and
  `DecisionEngine` depend on `NotificationProvider` (an ABC), never on Telegram directly.
  Adding a second channel (SMS, email, WhatsApp) means a new provider class, not a change
  to the decision or alert layers — this is exactly how the Telegram provider replaced an
  earlier WhatsApp one without touching any of the layers above it. See
  [app/notifications/base.py](app/notifications/base.py).
- **Two isolated Telegram bots for IPO and F&O alerts, routed deterministically.**
  `NotificationRouter` ([app/notifications/router.py](app/notifications/router.py)) reads
  `alert_category` off the alert's `feature_snapshot` and picks one of three independently
  configured `TelegramProvider` instances — one bot's credentials never leak into
  another's, and there is no fallback: an IPO alert whose IPO bot is down stays
  `PENDING`/`RETRYING` on that channel rather than being rerouted to the F&O bot (or vice
  versa). `breakout_v1` alerts predate the split and keep using the original default bot.
  See `IPO_TELEGRAM_*`/`FNO_TELEGRAM_*` in [.env.example](.env.example).
- **The queue is what keeps a slow Telegram API from blocking the scanner.**
  `AlertManager.process()` only ever awaits a fast in-memory `AlertQueue.put` — the actual
  HTTP call to Telegram happens later, off `NotificationManager`'s own consumer loop.
  `TelegramProvider` retries transient failures (timeouts, 429 rate limits, 5xx) with
  exponential backoff up to `TELEGRAM_MAX_RETRIES`, and treats other 4xx responses (bad
  chat id, unauthorized bot token, etc.) as permanent failures it doesn't retry.
- **Restart recovery, not resend.** On startup, `NotificationManager.recover_pending()`
  reloads every `PENDING`/`RETRYING` alert from PostgreSQL and redrives delivery —
  `deliver_now` checks `Alert.status` first, so anything already `SENT` is never resent.
  The in-memory `AlertQueue` itself is not persisted; PostgreSQL is the source of truth
  it's rebuilt from after a restart.
- **Never invents trading levels.** The notification message (`app/alerts/formatter.py`)
  only renders values actually present in the alert's `feature_snapshot`/`breakout_level`
  — no synthesized entry/stop-loss/target prices, and any missing field is omitted rather
  than guessed.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | App-level liveness, plus per-subsystem status (database, market_data, feature_engine, scanner, decision_engine, alert_queue, telegram) |
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
| `GET /alerts?symbol=&limit=50` | Recent alerts, optionally filtered by symbol |
| `GET /alerts/recent?limit=20` | Most recent alerts across all symbols |
| `GET /alerts/status` | Total/sent/pending/failed alert counts, last alert time |
| `GET /alerts/{id}` | One alert's full record (score, quality, levels, status, fingerprint) |
| `GET /decisions/{symbol}` | Live Decision Engine re-evaluation of the symbol's latest scanner result, with the full rule-by-rule breakdown |
| `GET /candidates?universe=&alert_category=&status=&limit=` | IPO/F&O candidate list (auth required) |
| `GET /candidates/{symbol}/explain` | Full explainability breakdown for one candidate — scores, factor-level breakdowns, WHY narrative (auth required) |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` | Dashboard authentication |
| `GET/POST/PATCH/DELETE /admin/users/*` | Admin-only user management |
| `GET /dashboard` | The dashboard itself (static page; login gate is client-side, real access control is on the API above) |

## Dashboard, auth, and the F&O/IPO explainability API (Phase 6)

**Live dashboard:** https://marketintel.129.225.91.54.nip.io/dashboard — publicly-trusted HTTPS (Let's Encrypt, no browser warning). The bare IP (`https://129.225.91.54/`) still works too, via a self-signed cert — see "HTTPS / nginx" below.

```
Browser
   │  HTTPS (nginx — see "HTTPS / nginx" below)
   ▼
GET /dashboard  →  app/web/dashboard.html (vanilla JS, no build step)
   │
   ├── POST /auth/login  →  UserService.authenticate() → SessionStore (Redis) → Set-Cookie: session_id
   ├── GET  /candidates, /candidates/{symbol}/explain  →  CandidateService  →  app.candidates.explainer
   └── ADMIN only: /admin/users/*  →  UserService (create/disable/enable/reset-password/delete)
```

- **Every score is traceable, never a black box.** `GET /candidates/{symbol}/explain` returns the Fundamental/Technical/Overall scores broken down by category (`Trend: 24.17/25`, ...) down to individual factors — each with its actual value, normalized sub-score, weight, contribution, and a plain-English reason — plus a deterministic WHY THIS STOCK / WHY NOW / WHAT CONFIRMS / WHAT HAS NOT BEEN CONFIRMED / RISKS narrative (`app/candidates/explainer.py`). None of this is AI-generated — it's a direct read of what `app.fundamentals.scorer`/`app.technical.scorer`/the Decision Engine already computed, just formatted. When the Fundamental Score is UNKNOWN, the Overall Score's breakdown explicitly says so rather than silently treating it as 0% weighted.
- **Sessions live in Redis, not as self-contained JWTs** (`app/auth/session_store.py`) — real logout and server-side expiry/revocation need a session record the server can delete on demand.
- **Passwords are bcrypt-hashed** (`app/auth/passwords.py`) and never logged; the shared request-logging middleware only ever logs method/path/status/duration.
- **Two roles, enforced server-side.** `require_admin` (`app/auth/dependencies.py`) gates every `/admin/users/*` route; a VIEWER gets a 403, not a degraded UI. `/candidates*`, `/alerts*`, `/decisions/*` require *any* logged-in session — an anonymous internet visitor gets 401, not the data.
- **The initial admin is never hardcoded.** `scripts/create_admin.py` is a one-off, manually-run bootstrap (`docker compose exec app python -m scripts.create_admin --email ... --name ...`) that prints a one-time password exactly once and forces a password change on first login.
- **`/health` stays unauthenticated on purpose** — it's the Docker healthcheck target; liveness probes are conventionally public in virtually every production system.

### HTTPS / nginx

The app itself is plain HTTP on `:8000`; nginx terminates TLS in front of it. Config: `/etc/nginx/sites-available/market-intel.conf` on the VM. Two hostnames are served, each with its own certificate (nginx picks the right one via SNI):

- **`marketintel.129.225.91.54.nip.io`** — a free [nip.io](https://nip.io) hostname that resolves to the VM's IP (no domain purchase needed), fronted by a **real, publicly-trusted Let's Encrypt certificate** (`/etc/letsencrypt/live/marketintel.129.225.91.54.nip.io/`, auto-renewed by certbot's systemd timer). This is the recommended URL — no certificate warning.
- **`129.225.91.54`** (bare IP) — kept working as a fallback via the original **self-signed** cert (`/etc/nginx/ssl/market-intel-selfsigned.crt`), since Let's Encrypt cannot issue certificates for bare IP addresses. Browsers will show the usual self-signed warning here.

`SESSION_COOKIE_SECURE=true` (the default) means dashboard login only works over HTTPS — a real browser won't send a `Secure` cookie back over plain HTTP.

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

Requires **Python 3.12** specifically (see `pyproject.toml`'s `requires-python`)
— `psycopg2-binary` has no prebuilt wheel for newer Python versions and will
fail to build from source without a local `pg_config`.

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

Telegram delivery is tested against `httpx.MockTransport` (`tests/test_telegram_provider.py`)
— no live credentials or network calls are needed to run the suite, including the
timeout/rate-limit/permanent-failure/retry-exhaustion scenarios.

## Configuration

All configuration is environment-driven via `app/config/settings.py`
(`pydantic-settings`), sourced from a `.env` file or real environment variables. See
[.env.example](.env.example) for the full list of variables (app, server, database,
Redis, logging, scheduler, 5paisa, scanner, decision, alert, market session, Telegram).

Telegram credentials (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` for the default bot,
`IPO_TELEGRAM_BOT_TOKEN`/`IPO_TELEGRAM_CHAT_ID` and `FNO_TELEGRAM_BOT_TOKEN`/
`FNO_TELEGRAM_CHAT_ID` for the two candidate-alert bots) can each be left blank
independently in dev — alerts are still created, persisted, and queued normally; only
the actual delivery attempt on that specific channel is skipped (logged, not sent)
until its own bot is configured. `GET /health` reports each bot's status separately
(`telegram`, `telegram_ipo`, `telegram_fno`).

## What's next (out of scope for Phase 5)

Additional scanners (VCP, Momentum, ORB, IPO), a dashboard, AI-driven analysis, and
order placement are deliberately not implemented yet — this phase is the Decision &
Alert Engine v1 (Telegram only) sitting on top of the existing Breakout Scanner v1.
