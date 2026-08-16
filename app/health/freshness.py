"""Data-freshness guard: is `daily_features` actually keeping up with
`daily_prices`, or has the feature-recompute step silently stopped
advancing while price ingestion kept going?

This is a real failure mode this project hit live: `daily_prices` stayed
current while `daily_features` sat frozen for several trading days,
because `FeatureEngine.run_daily()` only runs when `PipelineWorker`
receives a Redis event, and events dry up whenever the market is closed
or the local dev stack isn't running continuously. Every LISTED scanner
(breakout_v1/vcp_v1/momentum_v1/orb_v1) keys its own dedup date directly
off `daily_features`, so they silently stopped producing new results;
the F&O/IPO candidate scanners kept producing fresh-dated rows (their
scan_date is wall-clock-based) while silently scoring against the same
stale technical features underneath — the more dangerous half of this
failure mode, since nothing about their output *looked* wrong.

This module answers one question only, cheaply (two aggregate queries,
no per-symbol scan): are the two tables' own latest dates still in a
sane relationship with each other? It does not know or care what "today"
is on the wall clock — that keeps it meaningful on a closed-market day
and immune to timezone edge cases, and keeps it from flagging false
staleness in tests that seed both tables with the same fictional date.
"""

from dataclasses import dataclass
from datetime import date as date_

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository


@dataclass
class FreshnessStatus:
    daily_prices_date: date_ | None
    daily_features_date: date_ | None
    lag_days: int | None
    is_stale: bool
    reason: str


async def check_feature_freshness(session: AsyncSession, settings: Settings) -> FreshnessStatus:
    """Compares `daily_prices`' and `daily_features`' own latest dates
    (both filtered to active symbols, so they're directly comparable).

    Never raises on the "no data yet" case — a freshly-created database
    with no price history at all is a different situation from "features
    stopped advancing," and callers (health check, scanner engine) must
    not conflate the two into the same alarm.
    """
    prices_date = await PriceRepository(session).get_latest_date_across_active_symbols()
    features_date = await DailyFeatureRepository(session).get_latest_date_across_active_symbols()

    if prices_date is None:
        return FreshnessStatus(
            daily_prices_date=None,
            daily_features_date=features_date,
            lag_days=None,
            is_stale=False,
            reason="no daily_prices data yet — nothing to compare against",
        )

    if features_date is None:
        return FreshnessStatus(
            daily_prices_date=prices_date,
            daily_features_date=None,
            lag_days=None,
            is_stale=True,
            reason=(
                f"daily_prices has data through {prices_date} but "
                "daily_features has never been computed for any active symbol"
            ),
        )

    lag_days = (prices_date - features_date).days
    if lag_days > settings.feature_freshness_max_lag_days:
        return FreshnessStatus(
            daily_prices_date=prices_date,
            daily_features_date=features_date,
            lag_days=lag_days,
            is_stale=True,
            reason=(
                f"daily_features ({features_date}) is {lag_days} day(s) behind "
                f"daily_prices ({prices_date}) — exceeds "
                f"feature_freshness_max_lag_days={settings.feature_freshness_max_lag_days}"
            ),
        )

    return FreshnessStatus(
        daily_prices_date=prices_date,
        daily_features_date=features_date,
        lag_days=lag_days,
        is_stale=False,
        reason="within tolerance",
    )
