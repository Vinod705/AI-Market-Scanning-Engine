"""UniverseProvider: which symbols belong to which analysis universe.

Reuses existing data for IPO/LISTED — no new source. F&O is the one
genuinely new persistent classification (see `app.models.fno_universe`),
refreshed daily from `FivePaisaProvider.get_fno_symbol_roots()` — see
`app.scheduler.universe_jobs`.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository


class UniverseProvider:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._symbol_repo = SymbolRepository(session)
        self._fno_repo = FnoUniverseRepository(session)

    async def get_ipo_universe(self) -> list[Symbol]:
        """Symbols whose *latest* computed daily feature row has
        `pattern_ipo_base=True` — the existing Phase 3 IPO-base heuristic
        (see `app/features/patterns/calculator.py`), kept working exactly
        as-is. Documented limitation carried over unchanged: this is a
        "short price history + tight base" proxy, not a verified listing
        date, since 5paisa's scrip master doesn't reliably provide one."""
        latest_dates = (
            select(DailyFeature.symbol_id, func.max(DailyFeature.date).label("max_date"))
            .group_by(DailyFeature.symbol_id)
            .subquery()
        )
        stmt = (
            select(Symbol)
            .join(DailyFeature, DailyFeature.symbol_id == Symbol.id)
            .join(
                latest_dates,
                (DailyFeature.symbol_id == latest_dates.c.symbol_id)
                & (DailyFeature.date == latest_dates.c.max_date),
            )
            .where(DailyFeature.pattern_ipo_base.is_(True))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_fno_universe(self) -> list[Symbol]:
        symbol_ids = await self._fno_repo.list_symbol_ids()
        return await self._symbol_repo.list_by_ids(symbol_ids)

    async def get_listed_universe(self) -> list[Symbol]:
        return await self._symbol_repo.list_active()
