"""ORB Scanner v1: Opening Range Breakout, LISTED universe.

Explicit project definition (given, not inferred):

- Opening range = `SessionFeature.opening_range_high`/`opening_range_low`,
  already computed by `SessionFeatureCalculator` as the first 15 minutes
  from market open (`app/features/session/calculator.py`). Not recomputed
  or changed here.
- Bullish: latest price strictly ABOVE `opening_range_high`. No buffer.
- Bearish: latest price strictly BELOW `opening_range_low`. No buffer.
- No volume confirmation.
- Timing: valid only once the opening range has completed, for the same
  trading day. Enforced without inventing a new wall-clock cutoff by
  reusing two facts already true of the existing data:
  1. `opening_range_high`/`opening_range_low` are only non-None once the
     opening-range window's bars exist in `SessionFeature` — the same
     "not computed yet" signal every other feature already uses.
  2. "Latest price" here is `DailyPrice.close` (see `ScanContext` in
     `app/scanner/models.py` — the same daily-close convention every other
     LISTED-universe scanner already uses), which is only written once per
     day, after market close (`app/scheduler/jobs.py`'s `_daily_job`, cron
     16:00). Requiring `DailyPrice.date == SessionFeature.date` therefore
     both (a) guarantees the opening range's 15 minutes have elapsed by
     the time that day's close exists, and (b) is the literal "same
     trading day" check — with no new threshold invented.

Scoring reuses the exact same weighted-composite formula
`BreakoutScanner`/`VcpScanner`/`MomentumScanner` all use (byte-for-byte,
same `scanner_score_weight_*` settings) — the project's shared "how good
is this setup overall" convention, independent of the qualification
verdict. `DailyFeature` is optional here (ORB's own qualification never
requires it); when absent, every score component falls back to its
existing None-safe default, identical to how the other three scanners
already handle missing `DailyFeature` fields.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.daily_feature import DailyFeature
from app.models.session_feature import SessionFeature
from app.models.symbol import Symbol
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository
from app.scanner.base_scanner import BaseScanner
from app.scanner.models import ScannerContext, ScanOutcome, ValidationResult


@dataclass
class OrbScanContext:
    """Separate context shape from `ScanContext` — ORB's qualification is
    keyed off `SessionFeature`, not `DailyFeature`. Implements the
    `ScannerContext` Protocol structurally, same precedent as
    `app.candidates.models.CandidateContext`."""

    symbol: Symbol
    session_feature: SessionFeature
    price: Decimal | None
    price_date: date | None
    daily_feature: DailyFeature | None  # score() only — see module docstring

    @property
    def scan_date(self) -> date:
        return self.session_feature.date

    def feature_snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "price": str(self.price) if self.price is not None else None,
            "price_date": self.price_date.isoformat() if self.price_date else None,
        }
        for column in self.session_feature.__table__.columns:
            if column.name in {"id", "symbol_id", "created_at", "updated_at"}:
                continue
            value = getattr(self.session_feature, column.name)
            if isinstance(value, Decimal):
                value = str(value)
            elif isinstance(value, datetime | date):
                value = value.isoformat()
            snapshot[column.name] = value
        return snapshot


class OrbScanner(BaseScanner):
    name = "orb_v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def build_context_bulk(
        self, session: AsyncSession, symbols: list[Symbol]
    ) -> dict[int, ScannerContext | None]:
        """Same bulk-read pattern as `BreakoutScanner`/`VcpScanner`/
        `MomentumScanner` — ORB also scans the full LISTED universe, so it
        needs the same fix: fixed number of queries for the whole batch
        instead of one (or more) per symbol."""
        symbol_ids = [s.id for s in symbols]
        session_features_by_id = await SessionFeatureRepository(session).get_latest_bulk(
            symbol_ids
        )
        prices_by_id = await PriceRepository(session).get_latest_daily_bulk(symbol_ids)
        daily_features_by_id = await DailyFeatureRepository(session).get_latest_bulk(symbol_ids)

        contexts: dict[int, ScannerContext | None] = {}
        for symbol in symbols:
            session_feature = session_features_by_id.get(symbol.id)
            if session_feature is None:
                contexts[symbol.id] = None
                continue
            latest_price = prices_by_id.get(symbol.id)
            contexts[symbol.id] = OrbScanContext(
                symbol=symbol,
                session_feature=session_feature,
                price=latest_price.close if latest_price else None,
                price_date=latest_price.date if latest_price else None,
                daily_feature=daily_features_by_id.get(symbol.id),
            )
        return contexts

    def validate(self, context: ScannerContext) -> ValidationResult:
        assert isinstance(context, OrbScanContext)
        if context.price is None:
            return ValidationResult(valid=False, reason="missing required field: price")
        session_feature = context.session_feature
        if session_feature.opening_range_high is None or session_feature.opening_range_low is None:
            return ValidationResult(
                valid=False, reason="opening range not yet available for this session"
            )
        if context.price_date != session_feature.date:
            return ValidationResult(
                valid=False,
                reason="price and opening range are not from the same trading day",
            )
        return ValidationResult(valid=True)

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, OrbScanContext)
        price = context.price
        opening_range_high = context.session_feature.opening_range_high
        opening_range_low = context.session_feature.opening_range_low
        # validate() already guaranteed all three are non-None.
        assert price is not None
        assert opening_range_high is not None
        assert opening_range_low is not None

        if price > opening_range_high:
            return ScanOutcome(
                qualified=True,
                reason=f"price {price} above opening_range_high {opening_range_high} (bullish ORB)",
            )
        if price < opening_range_low:
            return ScanOutcome(
                qualified=True,
                reason=f"price {price} below opening_range_low {opening_range_low} (bearish ORB)",
            )
        return ScanOutcome(
            qualified=False,
            reason=f"price {price} within opening range [{opening_range_low}, {opening_range_high}]",
        )

    def score(self, context: ScannerContext) -> float:
        """Identical formula to `BreakoutScanner.score()`/`VcpScanner.score()`/
        `MomentumScanner.score()` on purpose — see module docstring. Not
        ORB-specific: the shared "overall technical strength" composite."""
        assert isinstance(context, OrbScanContext)
        features = context.daily_feature
        settings = self._settings

        trend = float(features.trend_strength or 0) if features else 0.0
        momentum = (float(features.momentum_score or 0) + 100) / 2 if features else 50.0
        volume = (
            min(float(features.relative_volume or 0) / 2.0, 1.0) * 100 if features else 0.0
        )
        if features and features.atr_expansion:
            volatility = 100.0
        elif features and features.atr_contraction:
            volatility = 30.0
        else:
            volatility = 60.0
        relative_strength = (
            50.0
            if not features or features.rs_vs_nifty is None
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

    def _resistance_distance_pct(self, context: OrbScanContext) -> float | None:
        features = context.daily_feature
        resistance = features.resistance_level if features else None
        if resistance is None or context.price is None or resistance == 0:
            return None
        return abs(float(context.price) - float(resistance)) / float(resistance) * 100.0

    def _resistance_proximity_score(self, context: OrbScanContext) -> float:
        distance_pct = self._resistance_distance_pct(context)
        if distance_pct is None:
            return 0.0
        threshold = self._settings.scanner_breakout_resistance_proximity_pct
        if threshold <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 - (distance_pct / threshold) * 100.0))
