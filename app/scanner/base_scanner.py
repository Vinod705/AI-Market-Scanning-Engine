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

from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.models import ScanContext, ScanOutcome, ValidationResult


class BaseScanner(ABC):
    """Strategy interface for a single scanning algorithm."""

    name: str

    @abstractmethod
    def validate(self, context: ScanContext) -> ValidationResult:
        """Check `context` has what this scanner needs. Called before `scan()`;
        if invalid, `scan()`/`score()` are never called for that symbol."""
        raise NotImplementedError

    @abstractmethod
    def scan(self, context: ScanContext) -> ScanOutcome:
        """Apply the scanner's rule set. Returns whether the symbol qualifies
        and a human-readable reason (which rules passed/failed)."""
        raise NotImplementedError

    @abstractmethod
    def score(self, context: ScanContext) -> float:
        """A 0-100 composite score, independent of the qualified/rejected
        verdict — even a rejected symbol gets a score, so a scanner_results
        row is always comparable to others."""
        raise NotImplementedError

    async def save_results(
        self,
        repository: ScannerResultRepository,
        context: ScanContext,
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
