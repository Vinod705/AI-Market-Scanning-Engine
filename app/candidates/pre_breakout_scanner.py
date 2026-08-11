"""Pre-Breakout Scanner — shared across the F&O and IPO universes.

Qualifies a symbol in `SetupState.PRE_BREAKOUT` (approaching resistance,
not through it yet — see `app.candidates.builder._detect_setup_state`)
with a minimum Overall Setup Score. The alert category depends on which
universe the candidate came from (`FNO_PRE_BREAKOUT` vs `IPO_PRE_BREAKOUT`)
— one scanner, not two near-duplicates, since the qualification logic
itself doesn't differ by universe.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.builder import build_candidate
from app.candidates.models import AlertCategory, CandidateContext, SetupState, Universe
from app.candidates.scanner_base import CandidateScannerBase
from app.candidates.sources import CandidateSourceProvider
from app.config.settings import Settings
from app.models.symbol import Symbol
from app.scanner.models import ScannerContext, ScanOutcome
from app.universe.provider import UniverseProvider


class PreBreakoutScanner(CandidateScannerBase):
    name = "pre_breakout_v1"

    def __init__(
        self,
        settings: Settings,
        source_providers: list[CandidateSourceProvider] | None = None,
    ) -> None:
        super().__init__(settings, source_providers)
        # Populated by `get_candidate_symbols` at the start of each scan
        # run; `build_context` reads it to resolve each symbol's universe
        # (IPO vs F&O) since this one scanner evaluates both.
        self._ipo_symbol_ids: set[int] = set()

    @property
    def universe(self) -> Universe:
        # This scanner evaluates both universes in one pass — the actual
        # per-symbol universe is resolved in `build_context` from
        # `_ipo_symbol_ids`, populated by `get_candidate_symbols`. This
        # property exists only to satisfy `CandidateScannerBase`'s
        # contract and is not otherwise used.
        return Universe.FNO

    async def get_candidate_symbols(
        self, session: AsyncSession, all_symbols: list[Symbol]
    ) -> list[Symbol]:
        provider = UniverseProvider(session, self._settings)
        fno_symbols = await provider.get_fno_universe()
        ipo_symbols = await provider.get_ipo_universe()
        self._ipo_symbol_ids = {s.id for s in ipo_symbols}

        merged: dict[int, Symbol] = {s.id: s for s in fno_symbols}
        for symbol in ipo_symbols:
            merged.setdefault(symbol.id, symbol)
        return list(merged.values())

    async def build_context(self, session: AsyncSession, symbol: Symbol) -> ScannerContext | None:
        universe = Universe.IPO if symbol.id in self._ipo_symbol_ids else Universe.FNO

        result = await build_candidate(
            symbol=symbol,
            universe=universe,
            scanner_name=self.name,
            session=session,
            fundamental_scorer=self._fundamental_scorer,
            technical_scorer=self._technical_scorer,
            settings=self._settings,
            source_providers=self._source_providers,
        )
        if result.candidate is None:
            return None
        return CandidateContext(symbol=symbol, candidate=result.candidate)

    def scan(self, context: ScannerContext) -> ScanOutcome:
        assert isinstance(context, CandidateContext)
        candidate = context.candidate
        settings = self._settings

        checks = {
            "setup_state_is_pre_breakout": candidate.setup_state == SetupState.PRE_BREAKOUT,
            "overall_score>=threshold": candidate.overall_score >= settings.pre_breakout_min_score,
        }
        qualified = all(checks.values())

        candidate.passed_rules = [name for name, passed in checks.items() if passed]
        candidate.failed_rules = [name for name, passed in checks.items() if not passed]
        candidate.alert_category = (
            (
                AlertCategory.IPO_PRE_BREAKOUT
                if candidate.universe == Universe.IPO
                else AlertCategory.FNO_PRE_BREAKOUT
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
