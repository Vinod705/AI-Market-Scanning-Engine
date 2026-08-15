"""Momentum Scanner v1: LISTED-universe momentum setup.

Qualification is `DailyFeature.momentum_score >= 50` alone — no other
condition. `momentum_score` is already computed by
`app.features.momentum.calculator.MomentumFeatureCalculator` (documented
there as a -100..+100 heuristic blend of RSI distance from 50 and
normalized MACD histogram, "a single sortable momentum number"); this
scanner adds no new calculation, just a qualification gate on top of an
already-computed, already-documented signal — the exact threshold (50)
and universe scope (LISTED) were explicitly specified by the user, not
inferred or invented.

This is a different, separate concept from `SetupState.MOMENTUM` (the
F&O/IPO candidate pipeline's resistance/ADX-based continuation state,
see `app.candidates.builder._detect_setup_state`) — that pipeline is not
touched here.

Scoring reuses the exact same general technical-strength composite
`BreakoutScanner`/`VcpScanner` use (same fields, same
`scanner_score_weight_*` settings) — the project's own shared "how good
is this setup overall" convention, not new scanner-specific logic.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.symbol import Symbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository
from app.scanner.base_scanner import BaseScanner
from app.scanner.models import ScanContext, ScannerContext, ScanOutcome, ValidationResult
from app.scanner.validator import ScannerValidator

_MOMENTUM_SCORE_QUALIFYING_THRESHOLD = 50.0


class MomentumScanner(BaseScanner):
    name = "momentum_v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def build_context_bulk(
        self, session: AsyncSession, symbols: list[Symbol]
    ) -> dict[int, ScannerContext | None]:
        """Same bulk-read pattern as `BreakoutScanner`/`VcpScanner` — this
        scanner also scans the full LISTED universe (default
        `get_candidate_symbols`, unmodified), so it needs the same fix:
        two queries for the whole batch instead of two per symbol."""
        symbol_ids = [s.id for s in symbols]
        features_by_id = await DailyFeatureRepository(session).get_latest_bulk(symbol_ids)
        prices_by_id = await PriceRepository(session).get_latest_daily_bulk(symbol_ids)

        contexts: dict[int, ScannerContext | None] = {}
        for symbol in symbols:
            features = features_by_id.get(symbol.id)
            if features is None:
                contexts[symbol.id] = None
                continue
            latest_price = prices_by_id.get(symbol.id)
            contexts[symbol.id] = ScanContext(
                symbol=symbol,
                features=features,
                price=latest_price.close if latest_price else None,
            )
        return contexts

    def validate(self, context: ScannerContext) -> ValidationResult:
        assert isinstance(context, ScanContext)
        # momentum_score is nullable on DailyFeature (until 26+ bars of
        # history exist for RSI/MACD warmup) — the one thing that can
        # genuinely be missing and that scan() needs.
        return ScannerValidator.require_fields(context, ["momentum_score"])

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, ScanContext)
        momentum_score = context.features.momentum_score
        assert momentum_score is not None  # validate() already required this
        qualified = float(momentum_score) >= _MOMENTUM_SCORE_QUALIFYING_THRESHOLD
        reason = (
            f"momentum_score={momentum_score} >= {_MOMENTUM_SCORE_QUALIFYING_THRESHOLD}"
            if qualified
            else f"momentum_score={momentum_score} < {_MOMENTUM_SCORE_QUALIFYING_THRESHOLD}"
        )
        return ScanOutcome(qualified=qualified, reason=reason)

    def score(self, context: ScannerContext) -> float:
        """Identical formula to `BreakoutScanner.score()`/`VcpScanner.score()`
        on purpose — see module docstring. Not momentum-specific: the
        shared "overall technical strength" composite."""
        assert isinstance(context, ScanContext)
        features = context.features
        settings = self._settings

        trend = float(features.trend_strength or 0)
        momentum = (float(features.momentum_score or 0) + 100) / 2
        volume = min(float(features.relative_volume or 0) / 2.0, 1.0) * 100
        volatility = (
            100.0 if features.atr_expansion else (30.0 if features.atr_contraction else 60.0)
        )
        relative_strength = (
            50.0
            if features.rs_vs_nifty is None
            else max(0.0, min(100.0, 50.0 + float(features.rs_vs_nifty)))
        )
        support_resistance = self._resistance_proximity_score(context)

        total = (
            trend * settings.scanner_score_weight_trend
            + momentum * settings.scanner_score_weight_momentum
            + volume * settings.scanner_score_weight_volume
            + volatility * settings.scanner_score_weight_volatility
            + relative_strength * settings.scanner_score_weight_relative_strength
            + support_resistance * settings.scanner_score_weight_support_resistance
        )
        return round(min(max(total, 0.0), 100.0), 2)

    # --- internals -----------------------------------------------------

    def _resistance_distance_pct(self, context: ScanContext) -> float | None:
        resistance = context.features.resistance_level
        if resistance is None or context.price is None or resistance == 0:
            return None
        return abs(float(context.price) - float(resistance)) / float(resistance) * 100.0

    def _resistance_proximity_score(self, context: ScanContext) -> float:
        distance_pct = self._resistance_distance_pct(context)
        if distance_pct is None:
            return 0.0
        threshold = self._settings.scanner_breakout_resistance_proximity_pct
        if threshold <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 - (distance_pct / threshold) * 100.0))
