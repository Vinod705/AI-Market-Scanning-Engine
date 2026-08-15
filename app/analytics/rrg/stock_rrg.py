"""Stock-vs-benchmark RRG: fetches one equity's and the benchmark's real
daily price history and runs them through `rrg_engine.compute_rrg_series`.

Benchmark defaults to `Settings.feature_rs_benchmark_symbol` ("NIFTY") —
the same benchmark `RelativeStrengthFeatureCalculator`/`DailyFeature.rs_vs_nifty`
already use, so a stock's RRG reading and its `rs_vs_nifty` feature are
directly comparable, not computed against two different baselines.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rrg.rrg_engine import compute_rrg_series, prices_to_frame
from app.analytics.rrg.rrg_models import RrgPoint
from app.config.settings import Settings
from app.repositories.market_repository import PriceRepository, SymbolRepository

_DEFAULT_LOOKBACK_DAYS = 250  # ~1 trading year — enough for two rounds of 14-period smoothing


async def compute_stock_rrg(
    session: AsyncSession,
    settings: Settings,
    symbol: str,
    *,
    benchmark_symbol: str | None = None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> list[RrgPoint] | None:
    """`None` when the symbol or the benchmark has no daily price history
    at all — never a fabricated/empty-series result treated as real data."""
    benchmark = benchmark_symbol or settings.feature_rs_benchmark_symbol

    symbol_repo = SymbolRepository(session)
    price_repo = PriceRepository(session)

    security_row = await symbol_repo.get_by_symbol(symbol)
    benchmark_row = await symbol_repo.get_by_symbol(benchmark)
    if security_row is None or benchmark_row is None:
        return None

    security_history = await price_repo.get_daily_history(security_row.id, limit=lookback_days)
    benchmark_history = await price_repo.get_daily_history(benchmark_row.id, limit=lookback_days)
    if not security_history or not benchmark_history:
        return None

    return compute_rrg_series(
        prices_to_frame(security_history),
        prices_to_frame(benchmark_history),
        symbol=symbol,
        benchmark_symbol=benchmark,
    )
