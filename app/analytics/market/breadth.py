"""Market breadth: advances/declines, up/down volume, % above moving
averages, and new highs/lows across the full active LISTED universe.

Bulk-only by design — `MarketDataCollector`/`ScannerManager`/`FeatureEngine`
all learned this the hard way earlier in this project (a per-symbol query
loop over ~9,598 symbols is the dominant cost of a pipeline cycle). Every
query here is one call for the whole universe:
`PriceRepository.get_daily_bulk_at_rank` (today's and yesterday's bar),
`DailyFeatureRepository.get_latest_bulk` (EMAs), and
`PriceRepository.get_52_week_high_low_bulk` (new highs/lows) — no loop
issues a query per symbol.
"""

from datetime import date as date_
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.market.market_models import MarketBreadthSnapshot
from app.core.time import utc_now
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository


def _pct(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator) * 100


async def compute_market_breadth(session: AsyncSession) -> MarketBreadthSnapshot:
    symbols = await SymbolRepository(session).list_active()
    symbol_ids = [s.id for s in symbols]

    price_repo = PriceRepository(session)
    today_prices = await price_repo.get_daily_bulk_at_rank(symbol_ids, rank=1)
    prior_prices = await price_repo.get_daily_bulk_at_rank(symbol_ids, rank=2)
    features = await DailyFeatureRepository(session).get_latest_bulk(symbol_ids)
    ranges_52wk = await price_repo.get_52_week_high_low_bulk(symbol_ids)
    # Reused only to measure coverage (see MarketBreadthSnapshot.pct_above_vwap's
    # docstring) — not used to compute a market-wide "% above VWAP" figure.
    session_features = await SessionFeatureRepository(session).get_latest_bulk(symbol_ids)
    vwap_coverage_symbols = sum(
        1 for sf in session_features.values() if sf.session_vwap is not None
    )

    advancing = declining = unchanged = 0
    up_volume = down_volume = 0
    symbols_with_ema20 = above_ema20 = 0
    symbols_with_ema50 = above_ema50 = 0
    symbols_with_ema200 = above_ema200 = 0
    new_highs = new_lows = 0

    as_of = None
    for symbol_id, today in today_prices.items():
        if as_of is None or today.date > as_of:
            as_of = today.date

        prior = prior_prices.get(symbol_id)
        if prior is not None:
            if today.close > prior.close:
                advancing += 1
                up_volume += today.volume
            elif today.close < prior.close:
                declining += 1
                down_volume += today.volume
            else:
                unchanged += 1

        feature = features.get(symbol_id)
        if feature is not None:
            if feature.ema20 is not None:
                symbols_with_ema20 += 1
                above_ema20 += today.close > feature.ema20
            if feature.ema50 is not None:
                symbols_with_ema50 += 1
                above_ema50 += today.close > feature.ema50
            if feature.ema200 is not None:
                symbols_with_ema200 += 1
                above_ema200 += today.close > feature.ema200

        range_52wk = ranges_52wk.get(symbol_id)
        if range_52wk is not None:
            high_52wk, low_52wk = range_52wk
            if today.high >= high_52wk:
                new_highs += 1
            if today.low <= low_52wk:
                new_lows += 1

    new_highs_lows_net_pct: Decimal | None = None
    if ranges_52wk:
        net_ratio = Decimal(new_highs - new_lows) / Decimal(len(ranges_52wk))
        net_ratio = max(Decimal(-1), min(Decimal(1), net_ratio))
        new_highs_lows_net_pct = 50 + net_ratio * 50

    return MarketBreadthSnapshot(
        as_of=as_of or date_.today(),
        computed_at=utc_now(),
        total_symbols=len(symbols),
        advancing=advancing,
        declining=declining,
        unchanged=unchanged,
        advance_decline_pct=_pct(advancing, advancing + declining),
        up_volume=up_volume,
        down_volume=down_volume,
        up_down_volume_pct=_pct(up_volume, up_volume + down_volume),
        symbols_with_ema20=symbols_with_ema20,
        pct_above_ema20=_pct(above_ema20, symbols_with_ema20),
        symbols_with_ema50=symbols_with_ema50,
        pct_above_ema50=_pct(above_ema50, symbols_with_ema50),
        symbols_with_ema200=symbols_with_ema200,
        pct_above_ema200=_pct(above_ema200, symbols_with_ema200),
        symbols_with_52wk_range=len(ranges_52wk),
        new_highs=new_highs,
        new_lows=new_lows,
        new_highs_lows_net_pct=new_highs_lows_net_pct,
        vwap_coverage_symbols=vwap_coverage_symbols,
        vwap_coverage_total=len(symbols),
    )
