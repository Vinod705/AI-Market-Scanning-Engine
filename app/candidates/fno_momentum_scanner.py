"""F&O Intraday Momentum Scanner.

Qualifies F&O-universe symbols already confirming or continuing a
breakout — `SetupState.BREAKOUT_CONFIRMED` or `SetupState.MOMENTUM` (see
`app.candidates.builder._detect_setup_state`) — with volume and
trend-strength confirmation and a minimum Overall Setup Score. Every
threshold comes from `Settings.fno_momentum_*`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.models import AlertCategory, CandidateContext, SetupState, Universe
from app.candidates.scanner_base import CandidateScannerBase, as_float
from app.models.symbol import Symbol
from app.scanner.models import ScannerContext, ScanOutcome
from app.universe.provider import UniverseProvider


class FnoMomentumScanner(CandidateScannerBase):
    name = "fno_momentum_v1"

    @property
    def universe(self) -> Universe:
        return Universe.FNO

    async def get_candidate_symbols(
        self, session: AsyncSession, all_symbols: list[Symbol]
    ) -> list[Symbol]:
        return await UniverseProvider(session).get_fno_universe()

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, CandidateContext)
        candidate = context.candidate
        settings = self._settings

        rvol = as_float(candidate.technical_feature_snapshot.get("relative_volume"))
        adx = as_float(candidate.technical_feature_snapshot.get("adx14"))

        checks = {
            "setup_state_is_momentum_or_confirmed": candidate.setup_state
            in (SetupState.MOMENTUM, SetupState.BREAKOUT_CONFIRMED),
            "relative_volume>=threshold": rvol is not None
            and rvol >= settings.fno_momentum_min_rvol,
            "adx>=threshold": adx is not None and adx >= settings.fno_momentum_min_adx,
            "overall_score>=threshold": candidate.overall_score >= settings.fno_momentum_min_score,
        }
        qualified = all(checks.values())

        candidate.passed_rules = [name for name, passed in checks.items() if passed]
        candidate.failed_rules = [name for name, passed in checks.items() if not passed]
        candidate.alert_category = (
            (
                AlertCategory.FNO_MOMENTUM
                if candidate.setup_state == SetupState.MOMENTUM
                else AlertCategory.FNO_BREAKOUT
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
