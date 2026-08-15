"""Sector RRG: one or more real NSE sectoral index symbols vs. a benchmark,
run through the same `rrg_engine.compute_rrg_series` `stock_rrg.py` uses.

`Symbol.sector` (the per-equity sector tag) is not populated by any
provider currently wired into this project (`UpstoxProvider.get_symbols()`
doesn't set it, and no data source for it exists — see
`app.features.relative_strength.calculator`'s own docstring, which
documents the same gap for `rs_vs_sector`/`sector_rank`). Grouping
individual stocks into sectors to build a synthetic sector composite would
require fabricating that classification, so this module does not do that.

Instead it reuses **real, already-collected sector INDEX price history** —
this project's `symbols` table already has genuine `daily_prices` for NSE's
published sectoral indices (e.g. "NIFTY IT", "BANKNIFTY", "NIFTY AUTO",
"NIFTY PHARMA", "NIFTY METAL", "NIFTY FMCG", "NIFTY REALTY", "NIFTY ENERGY",
"NIFTY PSU BANK", "NIFTY PVT BANK", "NIFTY HEALTHCARE", "NIFTY OIL AND GAS",
"NIFTY CONSR DURBL" — confirmed live this session, ~272 daily bars each),
seeded the same way the "NIFTY" benchmark symbol itself was. The caller
supplies which index symbols to treat as sectors — this module does not
hardcode a curated "the sectors" list, since which of the many NSE index
symbols count as a true sector (vs. a strategy/factor/bond index like
"NIFTY ALPHA 50" or "NIFTY GS 10YR") is a classification decision, not
something to silently bake in.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rrg.rrg_engine import compute_rrg_series, prices_to_frame
from app.analytics.rrg.rrg_models import RrgPoint
from app.config.settings import Settings
from app.repositories.market_repository import PriceRepository, SymbolRepository

_DEFAULT_LOOKBACK_DAYS = 250


async def compute_sector_rrg(
    session: AsyncSession,
    settings: Settings,
    sector_symbols: list[str],
    *,
    benchmark_symbol: str | None = None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict[str, list[RrgPoint]]:
    """One `sector_symbols` entry that has no matching `Symbol` row (or no
    price history) is simply absent from the result — never a fabricated
    empty series standing in for real data."""
    benchmark = benchmark_symbol or settings.feature_rs_benchmark_symbol

    symbol_repo = SymbolRepository(session)
    price_repo = PriceRepository(session)

    benchmark_row = await symbol_repo.get_by_symbol(benchmark)
    if benchmark_row is None:
        return {}
    benchmark_history = await price_repo.get_daily_history(benchmark_row.id, limit=lookback_days)
    if not benchmark_history:
        return {}
    benchmark_df = prices_to_frame(benchmark_history)

    results: dict[str, list[RrgPoint]] = {}
    for sector_symbol in sector_symbols:
        sector_row = await symbol_repo.get_by_symbol(sector_symbol)
        if sector_row is None:
            continue
        sector_history = await price_repo.get_daily_history(sector_row.id, limit=lookback_days)
        if not sector_history:
            continue
        results[sector_symbol] = compute_rrg_series(
            prices_to_frame(sector_history),
            benchmark_df,
            symbol=sector_symbol,
            benchmark_symbol=benchmark,
        )
    return results
