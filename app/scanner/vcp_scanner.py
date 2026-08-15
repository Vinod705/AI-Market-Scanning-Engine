"""VCP Scanner v1: Volatility Contraction Pattern setup.

The VCP price-contraction signal itself (`DailyFeature.pattern_vcp`) is
already computed by `app.features.patterns.calculator.PatternFeatureCalculator`
— documented there as "each successive 10-bar range tighter than the
last" (a rule-based approximation of Minervini's classic VCP shape).
This scanner adds no new pattern-detection logic of its own: qualification
is that flag, full stop — the smallest scanner that can sit on top of an
already-computed, already-documented signal.

Scoring reuses the exact same general technical-strength composite
`BreakoutScanner` uses (same fields, same `scanner_score_weight_*`
settings) — those are documented under the generic "Scanner engine:
composite score weights" header in `Settings`, not scoped to Breakout
Scanner specifically, so this is reuse of an existing, shared convention
("how good is this setup overall"), not new scanner-specific logic.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.symbol import Symbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository
from app.scanner.base_scanner import BaseScanner
from app.scanner.models import ScanContext, ScannerContext, ScanOutcome, ValidationResult
from app.scanner.validator import ScannerValidator


class VcpScanner(BaseScanner):
    name = "vcp_v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def build_context_bulk(
        self, session: AsyncSession, symbols: list[Symbol]
    ) -> dict[int, ScannerContext | None]:
        """Same bulk-read pattern as `BreakoutScanner.build_context_bulk` —
        this scanner also scans the full LISTED universe (default
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
        # pattern_vcp is a not-null boolean column — never itself missing.
        # `price` is the one thing that genuinely can be absent (no
        # daily_prices row yet) and is needed for a meaningful result.
        return ScannerValidator.require_fields(context, ["price"])

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, ScanContext)
        qualified = bool(context.features.pattern_vcp)
        reason = (
            "volatility contraction pattern detected (each successive 10-bar "
            "range tighter than the last)"
            if qualified
            else "no volatility contraction pattern on the latest bar"
        )
        return ScanOutcome(qualified=qualified, reason=reason)

    def score(self, context: ScannerContext) -> float:
        """Identical formula to `BreakoutScanner.score()` on purpose — see
        module docstring. Not VCP-specific: a shared "overall technical
        strength" composite."""
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
