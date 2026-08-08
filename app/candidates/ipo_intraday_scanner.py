"""IPO Intraday Scanner.

Qualifies IPO-universe symbols (see `app.universe.provider.UniverseProvider.get_ipo_universe`,
built on the existing Phase 3 `pattern_ipo_base` heuristic) already
confirming or continuing a breakout — `SetupState.BREAKOUT_CONFIRMED` or
`SetupState.MOMENTUM` — with volume confirmation and a minimum Overall
Setup Score. Deliberately does not require the EMA200-stack trend check
`breakout_v1` uses (see `app.decision.rules`): a recent IPO usually
doesn't have 200 days of trading history yet, so requiring it would
reject every IPO candidate by construction, not by genuine weakness.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.models import AlertCategory, CandidateContext, SetupState, Universe
from app.candidates.scanner_base import CandidateScannerBase, as_float
from app.models.symbol import Symbol
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
        return await UniverseProvider(session).get_ipo_universe()

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, CandidateContext)
        candidate = context.candidate
        settings = self._settings

        rvol = as_float(candidate.technical_feature_snapshot.get("relative_volume"))

        checks = {
            "setup_state_is_momentum_or_confirmed": candidate.setup_state
            in (SetupState.MOMENTUM, SetupState.BREAKOUT_CONFIRMED),
            "relative_volume>=threshold": rvol is not None
            and rvol >= settings.ipo_intraday_min_rvol,
            "overall_score>=threshold": candidate.overall_score >= settings.ipo_intraday_min_score,
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
