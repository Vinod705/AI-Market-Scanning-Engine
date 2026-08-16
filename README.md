# market-intelligence

A local, single-operator AI Market Scanning Engine for the Indian stock market (NSE) — never a hosted production service. Everything below describes the actual current implementation, not an aspirational roadmap.

## What this is, today

- **Data**: Upstox (REST + WebSocket) is the primary and only actively-configured market data provider. 5paisa (`FivePaisaProvider`) remains implemented as a secondary/legacy option — swapping providers means constructing a different class in `app/main.py`, nothing else changes. Upstox also supplies fundamentals (partial coverage) and the F&O/underlying-root universe data.
- **Pipeline**: two structurally independent trigger paths, both terminating in `AlertManager` → Telegram. Path A is event-driven (Redis Stream → `PipelineWorker` → FeatureEngine → all 7 scanners → `DecisionEngine`); Path B is a 60-second scheduled job (`MomentumPipelineCoordinator` → `SignalFusionEngine` → a momentum state machine). See "Decision & Alert architecture" below for why both exist and don't collide.
- **Seven scanners**, two families: LISTED-universe (`breakout_v1`, `vcp_v1`, `momentum_v1`, `orb_v1`) and F&O/IPO candidate scanners (`fno_momentum_v1`, `pre_breakout_v1`, `ipo_intraday_v1`).
- **Fundamentals**: a 14-factor scorer across 6 weighted categories, fed by whichever of Upstox/Trendlyne is configured (Upstox alone covers ~29% of factors — below the 50% completeness threshold, so the fundamental tier reads `UNKNOWN` by design in that configuration, never a fabricated score).
- **A read-only Market Dashboard** (`GET /dashboard`) covering market regime, sector rotation/RRG, momentum candidates, RVOL/volume leaders, OI buildup, fundamentals coverage, trigger history, and system health — backed by its own `/analytics/*` API, deliberately never triggering a live scanner calculation on request.
- **Telegram delivery** via three independently-configured bots (default / IPO / F&O), routed by `alert_category`.
- **A data-freshness guard** (`app/health/freshness.py`) comparing `daily_prices`' and `daily_features`' own latest dates, surfaced through `/health` — added after a real incident where `daily_features` silently stopped advancing for several days while `daily_prices` kept updating (see "Operational notes" below).

## Folder structure

```
market-intelligence/
├── app/
│   ├── api/               FastAPI routers: /health, /market/*, /features/*, /scanner/*,
│   │                      /alerts/*, /decisions/*, /candidates/*, /analytics/*, /auth/*, /admin/*
│   ├── config/            Pydantic Settings (env-driven configuration)
│   ├── core/               Cross-cutting concerns: logging, exceptions, middleware, time helpers
│   ├── data/               collector.py, validator.py, market_updater.py, ingestion_worker.py
│   ├── database/           SQLAlchemy async engine, session factory, declarative base
│   ├── health/             Data-freshness guard (app.health.freshness) — daily_prices vs
│   │                      daily_features staleness, consumed by /health and ScannerEngine
│   ├── features/           Feature engine: engine.py, calculator.py (aggregator), one
│   │                      calculator.py per category: trend/ momentum/ volatility/ volume/
│   │                      price_action/ structure/ support_resistance/ patterns/
│   │                      relative_strength/ session/
│   ├── candidates/         F&O/IPO candidate pipeline: builder.py (SetupState detection,
│   │                      overall_score), fno_momentum_scanner.py, pre_breakout_scanner.py,
│   │                      ipo_intraday_scanner.py, explainer.py (dashboard WHY narrative)
│   ├── universe/           UniverseProvider — LISTED (all active symbols), F&O (daily-
│   │                      refreshed join table), IPO (Symbol.listing_date-based)
│   ├── momentum/           Momentum state machine (SETUP→WATCH→ACTIVATING→TRIGGERED→
│   │                      CONFIRMED / EXHAUSTED / INVALIDATED) + MomentumStateEngine
│   ├── fundamentals/       FundamentalScorer (14 factors/6 categories), the multi-source
│   │                      orchestrator, Upstox/Trendlyne providers, the paced fetch queue
│   ├── technical/          TechnicalScorer — the candidate-scanner-side technical composite
│   ├── models/             symbols, daily_prices, intraday_prices, market_status,
│   │                      collector_logs, daily_features, session_features, scanner_results,
│   │                      scanner_runs, scanner_logs, alerts, alert_events,
│   │                      alert_delivery_logs, momentum_states, momentum_state_transitions,
│   │                      momentum_alert_observations, fundamental_snapshots,
│   │                      fundamental_fetch_log, fno_universe, market_data_feed_logs,
│   │                      market_regime_snapshots, sector_rrg_snapshots, oi_observations, users
│   ├── providers/          MarketDataProvider (ABC) + UpstoxProvider (primary, REST) +
│   │                      UpstoxMarketFeed (WebSocket) + FivePaisaProvider (secondary/legacy)
│   ├── repositories/       All SQLAlchemy queries live here (repository pattern)
│   ├── scanner/            LISTED-universe scanner engine: models.py, base_scanner.py,
│   │                      breakout_scanner.py, vcp_scanner.py, momentum_scanner.py,
│   │                      orb_scanner.py, scanner_registry.py, scanner_manager.py, engine.py
│   ├── decision/           DecisionEngine (event-driven path), evaluator.py, rules.py,
│   │                      momentum_pipeline_coordinator.py, momentum_decision_engine.py
│   │                      (the 60s scheduled path)
│   ├── alerts/             Alert layer: deduplicator.py (fingerprinting), throttler.py
│   │                      (cooldown), formatter.py, queue.py, manager.py — the single
│   │                      chokepoint both alert paths above call into
│   ├── notifications/      NotificationProvider (ABC) + TelegramProvider + NotificationRouter
│   │                      (default/IPO/F&O bot selection) + NotificationManager
│   ├── auth/                Session-based dashboard auth: passwords, session_store (Redis),
│   │                      dependencies (require_admin), redis_client
│   ├── pipeline/            Redis Stream event bus: events.py, queue.py, worker.py
│   │                      (the event-driven path's consumer)
│   ├── schemas/             Pydantic request/response schemas
│   ├── services/            market_service.py, feature_service.py, scanner_service.py,
│   │                      alert_service.py, decision_service.py, candidate_service.py — read
│   ├── scheduler/           APScheduler wrapper + market data / universe / fundamental-queue /
│   │                      alert-expiry / digest / analytics-snapshot / momentum-pipeline /
│   │                      momentum-observation-followup jobs
│   ├── web/                 dashboard.html (vanilla JS, no build step)
│   ├── utils/               Shared helpers
│   └── main.py               FastAPI app factory, lifespan, router + job registration
├── alembic/                  Migrations (wired to app settings)
├── tests/                    Pytest suite (aiosqlite-backed, no live DB/broker needed)
├── docker/                   Container entrypoint script
├── scripts/                  Operational scripts (create_admin, backfill_daily,
│                            backfill_ipo_listing_dates)
├── logs/                     Rotating log files (app.log, errors.log)
├── docs/                     Project documentation (architecture.md points back here)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt / requirements-dev.txt
├── pyproject.toml            black / ruff / mypy / pytest config
└── .env.example
```

## Tech stack

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy (async) · Alembic · Docker ·
Docker Compose · Redis (required — the event-driven ingestion→feature→scanner→decision
path runs over a Redis Stream) · APScheduler · Pydantic Settings · Loguru · Pandas ·
Pytest · Black · Ruff · Mypy

## Market data architecture

```
MarketDataProvider (ABC: connect/disconnect/is_connected/get_symbols/get_quote/get_intraday/get_daily)
        ↑
UpstoxProvider (primary, REST — instrument master, quotes, historical candles)
UpstoxMarketFeed (WebSocket — real-time ltpc ticks, TickAggregator batches into 1-min candles)
FivePaisaProvider (secondary/legacy, py5paisa SDK, TOTP auth)

MarketDataCollector → DataValidator → SymbolRepository/PriceRepository → PostgreSQL
        ↑                                        ↓
Scheduler jobs (daily EOD candles + symbol refresh, cron)   Universe reconciliation
        ↑                                        (see below)
UpstoxMarketFeed.run_forever() (WS, continuous, reconnects with backoff)
        ↓
Redis Stream "pipeline:ingestion_events" → PipelineWorker → FeatureEngine → ScannerEngine → DecisionEngine
```

- **Provider swap:** add a new class implementing `MarketDataProvider` and construct it instead of `UpstoxProvider` in `app/main.py` (`active_market_data_provider` setting selects which).
- **Universe reconciliation:** the symbol-refresh job doesn't just add/update symbols — after a *successful* fetch, `MarketDataCollector._reconcile_universe()` deactivates (`is_active=False`) symbols genuinely missing from the broker's current universe. Guarded against a partial/transient response: if the fetch returns fewer than `universe_reconciliation_min_fraction` (default 50%) of the previously-active count, deactivation is skipped entirely and logged as suspicious rather than risking a mass false-deactivation. A fetch that raises outright never reaches reconciliation at all.
- **LISTED / F&O / IPO universes:** three independently-computed, non-exclusive sets over the same `symbols` table (`app/universe/provider.py`). LISTED = every active symbol. F&O = a daily-refreshed join table (`fno_universe`), built from Upstox's F&O-segment instrument roots. IPO = `Symbol.listing_date` within the last `ipo_universe_max_age_years` (3) years — `listing_date` isn't populated by live ingestion, only by the one-off `scripts/backfill_ipo_listing_dates.py` against an external CSV.
- **Validation:** rejects negative prices/volume, missing/NaN/inf OHLC, illogical OHLC (e.g. high < low), and duplicate timestamps within a batch. See [app/data/validator.py](app/data/validator.py).
- **Failure isolation:** one symbol failing to collect doesn't abort the run — the collector logs it and continues, recording success/failure counts per run in `collector_logs`.
- **Market hours:** NSE regular session, Mon–Fri 09:15–15:30 IST (`MarketStatusUpdater.is_market_open`). Does not account for exchange holidays.

### Credentials

Set the `UPSTOX_*` variables in `.env` (see [.env.example](.env.example)) — `Settings.upstox_configured` gates whether the primary provider and its WebSocket feed connect at all. `FIVEPAISA_*` variables configure the secondary/legacy provider independently. Leave either blank in dev — the corresponding provider stays disconnected and scheduled jobs log a warning and skip themselves instead of crashing.

## Feature engine architecture

```
PostgreSQL (daily_prices / intraday_prices)
        ↓
FeatureEngine.run_daily() / run_session()  — driven by PipelineWorker (event-driven,
        ↓                                     not its own fixed-interval job)
9 category calculators (trend/momentum/volatility/volume/price_action/structure/
support_resistance/patterns/relative_strength) → daily_features
Session calculator (opening range, day high/low, intraday VWAP) → session_features
        ↓
app.health.freshness.check_feature_freshness — compares daily_prices' and
daily_features' own latest dates; surfaced via /health and a scanner-run log line
if daily_features falls more than feature_freshness_max_lag_days (default 2) behind
```

- **Never touches a broker.** Only reads OHLCV already validated and stored.
- **Vectorized, stateless recompute, idempotent.** Each run loads a bounded lookback window (`feature_daily_lookback_bars`, default 500 bars) and recomputes the whole series with pandas, then only *writes* rows newer than what's already stored (`DailyFeatureRepository.upsert` is keyed on `symbol_id, date` — re-running never creates a duplicate row, always updates in place).
- **Only runs when triggered.** `FeatureEngine.run_daily()` has no scheduler job of its own — it's called by `PipelineWorker._process()` on every Redis pipeline event. On a local dev machine that isn't running continuously (or on a day the market never produces new ticks), events dry up and `daily_features` can visibly lag behind `daily_prices` for as long as the stack stays idle. The freshness guard above exists specifically to make that visible rather than silent — see "Operational notes."
- **Chart patterns are heuristics, not ML** (including `pattern_vcp` — a rule-based tightening-range approximation of Minervini's VCP shape, not a canonical implementation of it). Documented in [app/features/patterns/calculator.py](app/features/patterns/calculator.py).
- **Known gaps, documented in code:** `rs_vs_nifty` stays `null` until a symbol named `NIFTY` exists in `symbols` (the NSE-equities-only instrument filter excludes indices). `rs_vs_sector`/`sector_rank` stay `null` — no sector data source exists.

## Scanner engine architecture

Two parallel scanner families share one `ScannerRegistry`/`ScannerEngine`/`ScannerManager`:

```
daily_features / session_features (read-only)          candidate context (SetupState,
        ↓                                                overall_score, from app.candidates.builder)
ScannerRegistry → BaseScanner subclasses                          ↓
   breakout_v1 / vcp_v1 / momentum_v1 / orb_v1           fno_momentum_v1 / pre_breakout_v1 /
   (LISTED universe, ScanContext)                        ipo_intraday_v1 (F&O/IPO, CandidateContext)
        ↓                                                          ↓
ScannerEngine.run_all() → freshness check (see above) → ScannerManager (per symbol: skip if
   already scanned for the date, else validate → scan → score → upsert scanner_results)
```

- **Strategy Pattern.** A new scanner is a `BaseScanner` subclass implementing `validate`/`scan`/`score`, registered in `app/main.py`. See [app/scanner/base_scanner.py](app/scanner/base_scanner.py).
- **No duplicate alerts.** `scanner_results` is unique on `(symbol_id, scanner_name, date)`. LISTED scanners key `date` off the underlying `daily_features` date; candidate scanners key it off wall-clock time at candidate-build time — a real, known asymmetry (see "Operational notes").
- **Two scoring formulas, deliberately not unified.** breakout_v1/vcp_v1/momentum_v1/orb_v1 share one weighted composite (Trend 25% / Momentum 20% / Volume 20% / Volatility 10% / Relative Strength 10% / Support-Resistance 15%, `Settings.scanner_score_weight_*`). fno_momentum_v1/pre_breakout_v1/ipo_intraday_v1 instead use `context.candidate.overall_score`, computed once at candidate-build time from `TechnicalScorer` (its own 6-factor weighting: Trend 25% / Momentum 20% / Volume 20% / Volatility 10% / VWAP 15% / Structure 10%) blended with the fundamental score (`fundamental*0.3 + technical*0.7`, falling back to pure technical when fundamentals are `UNKNOWN`).
- **Configurable thresholds, not hardcoded.** Every scanner's qualification thresholds — including `momentum_v1`'s `scanner_momentum_min_score` — live in `Settings` (`SCANNER_*` env vars).
- **Every symbol gets a score, qualified or not.** A rejected symbol's `scanner_results` row still carries a score, so rejects and qualifiers stay comparable. A symbol failing `validate()` never reaches `scan()` — logged to `scanner_logs`, no `scanner_results` row.

## Candidate pipeline (F&O / IPO)

`app/candidates/builder.py`'s `build_candidate()`: fetches the latest `daily_features` row and price, scores technicals via `TechnicalScorer`, detects `SetupState` (`PRE_BREAKOUT`/`BREAKOUT_CONFIRMED`/`MOMENTUM`, based on price's proximity to `resistance_level` plus RVOL/ADX confirmation), marks fundamentals `PENDING` (never fetched inline — a separate paced queue fills them in later so a 500+-candidate scan never fires that many fundamentals requests synchronously), and blends the two into `overall_score`. There's no separate `candidates` database table — this data lands in the same `scanner_results` table every scanner writes to (`feature_snapshot` JSON + `score`).

`ipo_intraday_v1` additionally accepts `PRE_BREAKOUT` (not just `BREAKOUT_CONFIRMED`/`MOMENTUM`) — live data showed almost no IPO-universe symbol sits at/above its own resistance level, so the stricter gate was producing zero qualified candidates regardless of the price/volume/score thresholds.

`AlertCategory` values (`app/candidates/models.py`): `IPO_PRE_BREAKOUT`, `IPO_BREAKOUT`, `IPO_MOMENTUM`, `FNO_PRE_BREAKOUT`, `FNO_BREAKOUT`, `FNO_MOMENTUM`. LISTED-scanner alerts carry no category and route through the default Telegram bot.

## Fundamental analysis

`app/fundamentals/scorer.py`'s `FundamentalScorer` scores exactly 14 factors across 6 weighted categories (Growth 20% / Profitability 20% / Financial Strength 20% / Cash Flow 15% / Valuation 15% / Ownership 10%). A factor missing its raw value is excluded from the weighted average entirely — never treated as zero. If overall data completeness falls below `fundamental_min_data_completeness_pct` (default 50%), the tier is forced to `UNKNOWN` and `score=None` regardless of what the raw weighted score would have been — **fundamentals are never fabricated**.

Data sources: `UpstoxFundamentalDataProvider` (configured whenever `UPSTOX_ACCESS_TOKEN` is set — covers P/E, ROE, ROCE, operating cash flow, revenue/profit, at most ~4 of the 14 scored factors) and `TrendlyneFundamentalDataProvider` (richer coverage via Trendlyne MCP tool calls — not configured unless `TRENDLYNE_MCP_URL` is set). `MultiSourceFundamentalProvider` merges field-by-field across whichever sources are active. A paced background queue (`app/fundamentals/queue_service.py`, every 5 minutes) fetches for qualified F&O/pre-breakout/IPO candidates — batched, rate-limit-aware, with an escalating cooldown on repeated rate-limit responses.

`overall_score = fundamental_score*0.3 + technical_score*0.7` when a fundamental score exists; falls back to pure `technical_score` when it doesn't (`UNKNOWN` or still-pending). Fundamentals are therefore optional/best-effort by design — never a hard gate on candidate qualification.

## Decision & Alert architecture — two paths, one alert manager

There are genuinely **two independent, concurrently-running trigger paths**, both real, both intentional (confirmed via code: they use structurally distinct scanner names/signal types that can never fingerprint-collide), both terminating in the same `AlertManager`:

```
Path A — event-driven, all 7 scanners' qualified results:
scanner_results (status=qualified, any scanner)
        ↓
DecisionEngine.run_all()  (called from PipelineWorker, once per Redis event)
   re-validates score/trend/RVOL/ADX/resistance-proximity/data-freshness against
   this engine's own DECISION_* thresholds
        ↓
ALERT-grade DecisionResult → AlertManager.process()

Path B — scheduled, momentum-state-driven (F&O/IPO candidates especially):
scanner_results (status=qualified)
        ↓
momentum_pipeline_run job, interval=60s, market-hours gated
        ↓
MomentumPipelineCoordinator → SignalFusionEngine.compute() → MomentumStateEngine.evaluate()
   state machine: SETUP → WATCH → ACTIVATING → TRIGGERED → CONFIRMED (or EXHAUSTED/INVALIDATED)
        ↓
if next_state in {TRIGGERED, CONFIRMED} → AlertManager.process()
   (also records momentum_alert_observations — trigger price/score/staleness,
   backfilled with real subsequent prices at 15m/1h/1d by a separate 5-minute job,
   never estimated)

Both paths → AlertManager
  → AlertDeduplicator (SHA-256 fingerprint: symbol+scanner_name+signal_type+level+date)
  → AlertThrottler (30-min cooldown per symbol+signal_type)
  → alerts (PostgreSQL, status=PENDING) → in-memory AlertQueue
        ↓
NotificationManager (queue consumer, runs forever)
        ↓
NotificationRouter.resolve(alert_category)
   ├── IPO_* categories   →  TelegramProvider (IPO bot)
   ├── FNO_* categories   →  TelegramProvider (F&O bot)
   └── no category (LISTED scanners) → TelegramProvider (default bot)
        ↓
alert_delivery_logs + alert_events (full audit trail) + Alert.status (SENT/FAILED/RETRYING)
```

- **Why two paths, not one.** Path A is the original "re-validate a scanner result against a second, independent rule layer" design (`DecisionEvaluator`), applying uniformly to all 7 scanners. Path B is the later momentum-state-machine addition — it tracks a symbol's *continuing* momentum through TRIGGERED→CONFIRMED transitions, a genuinely different concept from Path A's single-shot re-validation. `MomentumStateEngine`'s own docstring: "reuse, not reimplementation" — it calls the same `AlertManager.process()` everything else does, gaining fingerprint dedup and cooldown for free rather than reimplementing them.
- **They can't accidentally deduplicate each other** — and that's fine, by design: Path B's `scanner_name` is a fixed constant (`momentum_state_v1`), never equal to any of the 7 real scanner names, so its fingerprint never collides with Path A's.
- **A second, independent rule layer, not a rubber stamp.** `DecisionEvaluator` re-reads the scanner result's stored `feature_snapshot` and re-checks trend/RVOL/ADX/resistance itself against its own thresholds, plus criteria the scanner doesn't apply at all: minimum score, market-session validity, and data freshness.
- **ALERT / WATCH / REJECT.** Missing/stale data is an outright REJECT. Otherwise every core rule must pass *and* the market must be open for ALERT; anything short of that is WATCH, never silently dropped.
- **Known gap:** Path A's `data_freshness` rule judges a scanner result's own `scan_date` — for LISTED scanners that's the same date as the underlying `daily_features` row, so a stale-feature result is caught. For F&O/IPO candidate scanners, `scan_date` is wall-clock-based (always "today"), decoupled from the actual feature date it scored against — meaning a candidate scanner can pass this freshness check while still scoring against several-day-old technical features. `technical_feature_snapshot["feature_date"]` now carries the real date for a future fix; `DecisionEvaluator` doesn't yet consume it.
- **No duplicate alerts within one path, two ways.** Fingerprint dedup (same setup re-scanned every minute hashes identically, suppressed while a non-expired alert exists) plus a 30-minute cooldown per `(symbol, signal_type)`.
- **The notification provider is a swappable abstraction** (`app/notifications/base.py`) — `AlertManager`/`DecisionEngine` never talk to Telegram directly.
- **Restart recovery, not resend.** `NotificationManager.recover_pending()` reloads PENDING/RETRYING alerts from PostgreSQL on startup; `Alert.status` is checked before every send so nothing already `SENT` gets resent. The in-memory `AlertQueue` isn't persisted — PostgreSQL is the source of truth.
- **Never invents trading levels.** `app/alerts/formatter.py` only renders values actually present in the alert's `feature_snapshot` — no synthesized entry/stop-loss/target, and a missing field is omitted rather than guessed.

## Operational notes (from this project's own hardening pass)

- **`daily_features` staleness is a real, recurring failure mode on a non-continuously-running dev stack**, not a one-off bug: `FeatureEngine.run_daily()` only runs when `PipelineWorker` receives a Redis event, and events dry up whenever the market's closed or the container isn't up. The fix isn't in the recompute logic (it's correct — proven by running it and watching it catch up cleanly) but in *visibility*: `GET /health`'s `daily_features_freshness` field and a `ScannerEngine.run_all()` warning log now surface the gap loudly instead of it going unnoticed. If you see `daily_features_freshness: "stale"`, the fix is simply to let the app run through one more Redis event with fresh price data behind it — it self-heals, it just needs to actually run.
- **`Symbol.is_ipo` is a dormant, unused column** — `Symbol.listing_date` is the sole authoritative IPO-universe criterion (`UniverseProvider.get_ipo_universe`). Left in the schema rather than dropped via migration (an irreversible schema change wasn't judged worth making unilaterally); no code writes or reads it.
- **Universe reconciliation exists but is conservative on purpose.** A symbol only gets `is_active=False` after a *successful* fetch that returned at least `universe_reconciliation_min_fraction` (50%) of the previously-active count — a transient partial API response never mass-deactivates real symbols.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Per-subsystem status: database, market_data, scheduler, feature_engine/scanner/decision_engine (pipeline worker liveness), ingestion_worker, market_data_feed, alert_queue, telegram(+ipo/fno), tradingview, trendlyne_mcp, fundamental_queue, `daily_features_freshness`/`daily_features_latest_date`/`daily_prices_latest_date` (is the *data*, not just the process, current), system resource metrics |
| `GET /market/health`, `/market/status`, `/market/symbols`, `/market/latest/{symbol}` | Market data module — liveness, provider/market-open status, symbol master, latest candle |
| `GET /features/status`, `/features/latest/{symbol}`, `/features/history/{symbol}?limit=` | Feature engine reads |
| `GET /scanner/status`, `/scanner/results`, `/scanner/results/{symbol}`, `/scanner/runs` | Scanner engine reads, all 7 scanners |
| `GET /alerts`, `/alerts/recent`, `/alerts/status`, `/alerts/{id}` | Alert reads |
| `GET /decisions/{symbol}` | Live Decision Engine re-evaluation with the full rule-by-rule breakdown |
| `GET /candidates`, `/candidates/{symbol}/explain` | IPO/F&O candidate list + full explainability breakdown (auth required) |
| `GET /analytics/market-regime`, `/sector-rrg`, `/live-triggers`, `/provider-health`, `/momentum/candidates`, `/momentum/history`, `/volume-leaders`, `/oi-buildup`, `/fundamentals-coverage`, `/admin/fundamental-queue` | Read-only Market Dashboard API — consumes stored/cached analytics snapshots, never triggers a live scan (auth required) |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` | Dashboard authentication |
| `GET/POST/PATCH/DELETE /admin/users/*` | Admin-only user management |
| `GET /dashboard` | The dashboard itself (static page; real access control is on the API above) |

## Dashboard, auth, and explainability

**Live dashboard:** https://marketintel.129.225.91.54.nip.io/dashboard — publicly-trusted HTTPS (Let's Encrypt, no browser warning). The bare IP (`https://129.225.91.54/`) still works via a self-signed cert — see "HTTPS / nginx" below.

```
Browser
   │  HTTPS (nginx — see "HTTPS / nginx" below)
   ▼
GET /dashboard  →  app/web/dashboard.html (vanilla JS, no build step)
   │
   ├── POST /auth/login  →  UserService.authenticate() → SessionStore (Redis) → Set-Cookie: session_id
   ├── GET  /candidates, /candidates/{symbol}/explain  →  CandidateService → app.candidates.explainer
   ├── GET  /analytics/*  →  AnalyticsService — reads stored snapshots only, read/write paths
   │                        stay fully separate (Phase 15 requirement)
   └── ADMIN only: /admin/users/*  →  UserService (create/disable/enable/reset-password/delete)
```

- **Every score is traceable, never a black box.** `GET /candidates/{symbol}/explain` returns the Fundamental/Technical/Overall scores broken down by category and factor — value, normalized sub-score, weight, contribution, plain-English reason — plus a deterministic WHY THIS STOCK / WHY NOW / WHAT CONFIRMS / WHAT HAS NOT BEEN CONFIRMED / RISKS narrative. None of this is AI-generated — it's a direct read of what the scorers/Decision Engine already computed. An `UNKNOWN` Fundamental Score is reported as such, never silently treated as 0.
- **Sessions live in Redis, not self-contained JWTs** (`app/auth/session_store.py`) — real logout and server-side revocation need a session record the server can delete.
- **Passwords are bcrypt-hashed**, never logged.
- **Two roles, enforced server-side.** `require_admin` gates every `/admin/users/*` route; `/candidates*`, `/alerts*`, `/decisions/*`, `/analytics/*` require any logged-in session.
- **The initial admin is never hardcoded** — `scripts/create_admin.py` prints a one-time password exactly once and forces a change on first login.
- **`/health` stays unauthenticated on purpose** — it's the Docker healthcheck target.

### HTTPS / nginx

The app itself is plain HTTP on `:8000`; nginx terminates TLS in front of it. Two hostnames, each with its own certificate via SNI: `marketintel.129.225.91.54.nip.io` (real Let's Encrypt cert, recommended) and the bare IP `129.225.91.54` (self-signed fallback, since Let's Encrypt can't issue for bare IPs). `SESSION_COOKIE_SECURE=true` (the default) means dashboard login only works over HTTPS.

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
   curl http://localhost:8000/market/symbols
   # []   (until the symbol-refresh job or a manual collector run populates it)
   ```

4. Stop the stack:

   ```bash
   docker compose down
   ```

   Add `-v` to also drop the Postgres/Redis data volumes.

The app container runs `alembic upgrade head` on startup (see [docker/entrypoint.sh](docker/entrypoint.sh)) before launching Uvicorn.

## Running locally without Docker

Requires **Python 3.12** specifically (see `pyproject.toml`'s `requires-python`) — `psycopg2-binary` has no prebuilt wheel for newer Python versions and will fail to build from source without a local `pg_config`.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements-dev.txt
cp .env.example .env           # point POSTGRES_HOST/REDIS_HOST at localhost

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You'll need a local Postgres instance reachable at the host/port in `.env`, and Redis (required — the event-driven feature/scanner/decision path runs over it).

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

Telegram delivery is tested against `httpx.MockTransport` — no live credentials or network calls needed, including timeout/rate-limit/permanent-failure/retry-exhaustion scenarios.

## Configuration

All configuration is environment-driven via `app/config/settings.py` (`pydantic-settings`), sourced from a `.env` file or real environment variables. See [.env.example](.env.example) for the full list (app, server, database, Redis, logging, scheduler, Upstox, 5paisa, Trendlyne, scanner, decision, alert, market session, feature-freshness, universe reconciliation, Telegram).

Telegram credentials (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` for the default bot, `IPO_TELEGRAM_BOT_TOKEN`/`IPO_TELEGRAM_CHAT_ID` and `FNO_TELEGRAM_BOT_TOKEN`/`FNO_TELEGRAM_CHAT_ID` for the two candidate-alert bots) can each be left blank independently — alerts are still created, persisted, and queued normally; only the delivery attempt on that specific channel is skipped until its own bot is configured. `GET /health` reports each bot's status separately (`telegram`, `telegram_ipo`, `telegram_fno`).

## Out of scope

AI-driven analysis and order placement are deliberately not implemented — this project is the data + feature + scanning + decision/alert + read-only dashboard layer, nothing that places or recommends specific trades autonomously.
