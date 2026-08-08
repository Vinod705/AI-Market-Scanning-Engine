"""BaseScanner: the Strategy Pattern interface every scanner implements.

`validate`, `scan`, and `score` are abstract — that's where a scanner's
actual strategy differs from every other one. `save_results` is concrete
and shared: persistence is identical across scanners (upsert by symbol +
scanner name + date), so there's no reason for each subclass to reimplement
it, and no ORM/repository code leaks into strategy classes.

A new scanner (VCP, ORB, Gap, ...) is: subclass `BaseScanner`, implement
the three abstract methods, register an instance with `ScannerRegistry`.
Nothing in `ScannerManager` or `ScannerEngine` needs to change.
"""

from abc import ABC, abstractmethod
from datetime import date as date_
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.symbol import Symbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.models import ScanContext, ScannerContext, ScanOutcome, ValidationResult


class BaseScanner(ABC):
    """Strategy interface for a single scanning algorithm."""

    name: str

    async def get_candidate_symbols(
        self, session: AsyncSession, all_symbols: list[Symbol]
    ) -> list[Symbol]:
        """Symbols this scanner should evaluate. Defaults to every active
        symbol — `breakout_v1`'s existing, unchanged behavior. Override to
        scope to a narrower universe (see the F&O/IPO candidate scanners
        in `app/candidates/`, which only care about `UniverseProvider`'s
        FNO/IPO sets, not the full listed universe)."""
        return all_symbols

    async def build_context(self, session: AsyncSession, symbol: Symbol) -> ScannerContext | None:
        """Build the input `validate`/`scan`/`score` operate on for one
        symbol. Returns None to skip the symbol entirely (e.g. no features
        computed yet). Default: the DailyFeature + latest close price shape
        every scanner used before this hook existed — override to build a
        different context (see `app.candidates.models.CandidateContext`,
        which additionally needs fundamental/technical scoring and
        session-level data `ScanContext` doesn't carry)."""
        features = await DailyFeatureRepository(session).get_latest(symbol.id)
        if features is None:
            return None
        latest_price = await PriceRepository(session).get_latest_daily(symbol.id)
        return ScanContext(
            symbol=symbol, features=features, price=latest_price.close if latest_price else None
        )

    @abstractmethod
    def validate(self, context: ScannerContext) -> ValidationResult:
        """Check `context` has what this scanner needs. Called before `scan()`;
        if invalid, `scan()`/`score()` are never called for that symbol."""
        raise NotImplementedError

    @abstractmethod
    def scan(self, context: ScannerContext) -> ScanOutcome:
        """Apply the scanner's rule set. Returns whether the symbol qualifies
        and a human-readable reason (which rules passed/failed)."""
        raise NotImplementedError

    @abstractmethod
    def score(self, context: ScannerContext) -> float:
        """A 0-100 composite score, independent of the qualified/rejected
        verdict — even a rejected symbol gets a score, so a scanner_results
        row is always comparable to others."""
        raise NotImplementedError

    async def save_results(
        self,
        repository: ScannerResultRepository,
        context: ScannerContext,
        outcome: ScanOutcome,
        score: float,
        scan_date: date_,
    ) -> None:
        """Persist this scanner's verdict for `context.symbol` on `scan_date`.
        Shared by every scanner — see the module docstring for why this isn't
        abstract."""
        await repository.upsert(
            symbol_id=context.symbol.id,
            scanner_name=self.name,
            date=scan_date,
            score=Decimal(str(round(score, 2))),
            status="qualified" if outcome.qualified else "rejected",
            reason=outcome.reason,
            feature_snapshot=context.feature_snapshot(),
        )
