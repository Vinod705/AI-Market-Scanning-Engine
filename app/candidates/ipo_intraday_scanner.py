"""IPO Intraday Scanner.

Qualifies IPO-universe symbols (see `app.universe.provider.UniverseProvider.get_ipo_universe`,
real listing-date age, not a recency/chart-pattern proxy) already
confirming or continuing a breakout — `SetupState.BREAKOUT_CONFIRMED` or
`SetupState.MOMENTUM` — with volume confirmation and a minimum Overall
Setup Score, plus four price/volume filters against the stock's trailing
52-week High/Low (see `PriceRepository.get_52_week_high_low` — NOT "since
listing": our local daily_prices history is only ~400 days deep, so an
unbounded aggregate would silently just be a ~400-day figure for an older
IPO): price within `ipo_intraday_max_pct_below_52w_high`% below that
high, more than `ipo_intraday_min_pct_above_52w_low`% above that low,
priced above `ipo_intraday_min_price`, and traded above
`ipo_intraday_min_volume` shares. Deliberately does not require the
EMA200-stack trend check `breakout_v1` uses (see `app.decision.rules`):
an IPO within its first ~200 trading days won't have that history yet,
so requiring it would reject a genuine candidate by construction, not by
genuine weakness.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.models import AlertCategory, CandidateContext, SetupState, Universe
from app.candidates.scanner_base import CandidateScannerBase, as_float
from app.models.symbol import Symbol
from app.repositories.market_repository import PriceRepository
from app.scanner.models import ScannerContext, ScanOutcome
from app.universe.provider import UniverseProvider


class IpoIntradayScanner(CandidateScannerBase):
    name = "ipo_intraday_v1"

    @property
    def universe(self) -> Universe:
        return Universe.IPO

    async def get_candidate_symbols(
        self, session: AsyncSession, all_symbols: list[Symbol]
    ) -> list[Symbol]:
        return await UniverseProvider(session, self._settings).get_ipo_universe()

    async def build_context(self, session: AsyncSession, symbol: Symbol) -> ScannerContext | None:
        """Reuses the shared `CandidateScannerBase.build_context`, then adds
        two IPO-specific raw values `build_candidate` doesn't compute for
        anyone else: trailing 52-week high/low and the latest raw daily
        volume. Stashed in `technical_feature_snapshot` (the same
        free-form-extra pattern `build_candidate` already uses for
        `session_vwap`) rather than a new field on `StockCandidate`, and
        computed here rather than in the shared builder so F&O/pre-breakout
        are untouched."""
        context = await super().build_context(session, symbol)
        if context is None:
            return None
        assert isinstance(context, CandidateContext)
        candidate = context.candidate

        price_repo = PriceRepository(session)
        high_low = await price_repo.get_52_week_high_low(symbol.id)
        if high_low is not None:
            week_52_high, week_52_low = high_low
            candidate.technical_feature_snapshot["ipo_52w_high"] = str(week_52_high)
            candidate.technical_feature_snapshot["ipo_52w_low"] = str(week_52_low)

        latest_price = await price_repo.get_latest_daily(symbol.id)
        if latest_price is not None:
            candidate.technical_feature_snapshot["ipo_latest_volume"] = latest_price.volume

        return context

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, CandidateContext)
        candidate = context.candidate
        settings = self._settings

        rvol = as_float(candidate.technical_feature_snapshot.get("relative_volume"))
        price = float(candidate.price) if candidate.price is not None else None
        week_52_high = as_float(candidate.technical_feature_snapshot.get("ipo_52w_high"))
        week_52_low = as_float(candidate.technical_feature_snapshot.get("ipo_52w_low"))
        volume = as_float(candidate.technical_feature_snapshot.get("ipo_latest_volume"))

        pct_below_52w_high = (
            (week_52_high - price) / week_52_high * 100
            if week_52_high is not None and price is not None and week_52_high > 0
            else None
        )
        pct_above_52w_low = (
            (price / week_52_low - 1) * 100
            if week_52_low is not None and price is not None and week_52_low > 0
            else None
        )

        checks = {
            "setup_state_is_momentum_or_confirmed": candidate.setup_state
            in (SetupState.MOMENTUM, SetupState.BREAKOUT_CONFIRMED),
            "relative_volume>=threshold": rvol is not None
            and rvol >= settings.ipo_intraday_min_rvol,
            "overall_score>=threshold": candidate.overall_score >= settings.ipo_intraday_min_score,
            "price_within_pct_below_52w_high": pct_below_52w_high is not None
            and 0 < pct_below_52w_high < settings.ipo_intraday_max_pct_below_52w_high,
            "price_above_52w_low_multiple": pct_above_52w_low is not None
            and pct_above_52w_low > settings.ipo_intraday_min_pct_above_52w_low,
            "price>min_price": price is not None and price > settings.ipo_intraday_min_price,
            "volume>min_volume": volume is not None and volume > settings.ipo_intraday_min_volume,
        }
        qualified = all(checks.values())

        candidate.passed_rules = [name for name, passed in checks.items() if passed]
        candidate.failed_rules = [name for name, passed in checks.items() if not passed]
        candidate.alert_category = (
            (
                AlertCategory.IPO_MOMENTUM
                if candidate.setup_state == SetupState.MOMENTUM
                else AlertCategory.IPO_BREAKOUT
            )
            if qualified
            else None
        )
        candidate.reason = (
            "all conditions met: " + ", ".join(candidate.passed_rules)
            if qualified
            else f"failed {len(candidate.failed_rules)}/{len(checks)}: "
            + ", ".join(candidate.failed_rules)
        )
        return ScanOutcome(qualified=qualified, reason=candidate.reason)

    def score(self, context: ScannerContext) -> float:
        assert isinstance(context, CandidateContext)
        return context.candidate.overall_score
