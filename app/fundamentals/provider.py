"""FundamentalDataProvider: the abstraction the Fundamental Intelligence
Engine depends on instead of any specific data vendor.

Mirrors `app.notifications.base.NotificationProvider`'s role in this
codebase: swapping in a real, licensed fundamentals vendor later means
adding one new class here and wiring it in at startup — nothing in
`app.fundamentals.scorer`, the new scanners, or the Decision Engine
changes.
"""

from abc import ABC, abstractmethod

from app.fundamentals.models import FundamentalData


class FundamentalDataProvider(ABC):
    name: str

    @abstractmethod
    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        """Return the best-available fundamental data for `symbol`, or
        None if nothing is available at all. Partial data is expected and
        normal — every field on `FundamentalData` is optional; callers
        (see `app.fundamentals.scorer.FundamentalScorer`) must treat each
        missing field as UNKNOWN, never as zero."""
        raise NotImplementedError
