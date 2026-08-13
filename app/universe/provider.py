"""UniverseProvider: which symbols belong to which analysis universe.

Reuses existing data for IPO — no new source. F&O is the one genuinely
new persistent classification (see `app.models.fno_universe`), refreshed
daily from whichever active market-data provider implements the
F&O-roots provider contract — see
`app.scheduler.universe_jobs.FnoRootsProvider`.

LISTED has no method here: it's just every active symbol, which is
`BaseScanner.get_candidate_symbols()`'s own default
(`app/scanner/base_scanner.py`) — `BreakoutScanner` (`breakout_v1`) never
overrides it, so it evaluates the full active-symbol set unfiltered. A
`get_listed_universe()` wrapper around `SymbolRepository.list_active()`
used to exist here too, purely duplicating that default with an extra,
redundant DB query — removed as dead code (Phase 4C code-level audit
found it had zero real callers).
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.symbol import Symbol
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository

_DAYS_PER_YEAR = 365


class UniverseProvider:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._symbol_repo = SymbolRepository(session)
        self._fno_repo = FnoUniverseRepository(session)

    async def get_ipo_universe(self) -> list[Symbol]:
        """Symbols listed within the last `settings.ipo_universe_max_age_years`
        years, by real listing date.

        `Symbol.listing_date` must be populated from a verified external
        source (see `scripts/backfill_ipo_listing_dates.py`) — 5paisa's
        scrip master and Trendlyne's fundamental data were both checked
        live and neither reports a listing date, and our own price history
        (`daily_prices`, ~400 days deep) cannot express a multi-year
        window either. A symbol with `listing_date IS NULL` (unbackfilled
        or unmatched) is correctly excluded, not guessed into the universe
        — see the backfill script's docstring for why this must never be
        inferred from `created_at`, the first `daily_prices` bar, or bar
        count.
        """
        cutoff = date.today() - timedelta(
            days=_DAYS_PER_YEAR * self._settings.ipo_universe_max_age_years
        )
        stmt = select(Symbol).where(
            Symbol.is_active.is_(True),
            Symbol.listing_date.is_not(None),
            Symbol.listing_date >= cutoff,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_fno_universe(self) -> list[Symbol]:
        symbol_ids = await self._fno_repo.list_symbol_ids()
        return await self._symbol_repo.list_by_ids(symbol_ids)
