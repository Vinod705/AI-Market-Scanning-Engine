"""Shared base for the candidate-based scanners (`fno_momentum_v1`,
`pre_breakout_v1`, `ipo_intraday_v1`).

Factors out the one piece all three genuinely share: turning a `Symbol`
into a fully-scored `CandidateContext` via `app.candidates.builder.build_candidate`.
Universe selection (`get_candidate_symbols`) and qualification logic
(`validate`/`scan`/`score`) stay in each subclass since that's exactly
where the three scanners differ.
"""

from abc import abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.builder import build_candidate
from app.candidates.models import CandidateContext, Universe
from app.config.settings import Settings
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.scorer import FundamentalScorer
from app.models.symbol import Symbol
from app.scanner.base_scanner import BaseScanner
from app.scanner.models import ScannerContext, ValidationResult
from app.technical.scorer import TechnicalScorer


class CandidateScannerBase(BaseScanner):
    def __init__(
        self,
        settings: Settings,
        fundamental_provider: FundamentalDataProvider,
    ) -> None:
        self._settings = settings
        self._fundamental_provider = fundamental_provider
        self._fundamental_scorer = FundamentalScorer(settings)
        self._technical_scorer = TechnicalScorer(settings)

    @property
    @abstractmethod
    def universe(self) -> Universe:
        raise NotImplementedError

    async def build_context(self, session: AsyncSession, symbol: Symbol) -> ScannerContext | None:
        result = await build_candidate(
            symbol=symbol,
            universe=self.universe,
            scanner_name=self.name,
            session=session,
            fundamental_provider=self._fundamental_provider,
            fundamental_scorer=self._fundamental_scorer,
            technical_scorer=self._technical_scorer,
            settings=self._settings,
        )
        if result.candidate is None:
            return None
        return CandidateContext(symbol=symbol, candidate=result.candidate)

    def validate(self, context: ScannerContext) -> ValidationResult:
        assert isinstance(context, CandidateContext)
        candidate = context.candidate
        if candidate.setup_state is None:
            return ValidationResult(valid=False, reason="no identifiable setup state")
        snapshot = candidate.technical_feature_snapshot
        if snapshot.get("adx14") is None or snapshot.get("relative_volume") is None:
            return ValidationResult(valid=False, reason="missing adx14/relative_volume")
        return ValidationResult(valid=True)


def as_float(value: object) -> float | None:
    """Feature snapshots are JSON (Decimal values stringified), so every
    numeric read of `candidate.technical_feature_snapshot` goes through
    this rather than a bare cast — mirrors `DecisionValidator.as_float`."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
