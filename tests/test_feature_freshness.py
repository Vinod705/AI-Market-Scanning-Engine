"""Tests for app.health.freshness.check_feature_freshness.

Real production incident this guards against: daily_prices kept
advancing while daily_features silently stopped, because FeatureEngine
only runs off Redis events that dry up whenever the market's closed —
see app.health.freshness's module docstring."""

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.health.freshness import check_feature_freshness
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository

_FEATURES: dict[str, object] = {"trend_strength": "50"}


async def _seed_symbol_with_prices_and_features(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    price_dates: list[date],
    feature_dates: list[date],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="FRESH", exchange="N", instrument_token="fresh1")
        )
        await session.commit()

        await PriceRepository(session).upsert_daily_many(
            symbol_row.id,
            [
                Candle(
                    timestamp=datetime.combine(d, datetime.min.time()),
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=10_000,
                )
                for d in price_dates
            ],
        )
        for d in feature_dates:
            await DailyFeatureRepository(session).upsert(symbol_row.id, d, _FEATURES)
        await session.commit()


async def test_reports_not_stale_when_dates_match(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 11)],
        feature_dates=[date(2026, 8, 11)],
    )
    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.is_stale is False
    assert status.lag_days == 0
    assert status.daily_prices_date == date(2026, 8, 11)
    assert status.daily_features_date == date(2026, 8, 11)


async def test_reports_not_stale_within_tolerance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Default tolerance is 2 days — a normal one-day EOD-timing lag must
    not falsely trip the guard."""
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 11), date(2026, 8, 12)],
        feature_dates=[date(2026, 8, 11)],
    )
    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.is_stale is False
    assert status.lag_days == 1


async def test_reports_stale_beyond_tolerance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The real incident this guards against: daily_prices ran 3 trading
    days ahead of daily_features."""
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[
            date(2026, 8, 11),
            date(2026, 8, 12),
            date(2026, 8, 13),
            date(2026, 8, 14),
        ],
        feature_dates=[date(2026, 8, 11)],
    )
    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.is_stale is True
    assert status.lag_days == 3
    assert "daily_features" in status.reason
    assert "daily_prices" in status.reason


async def test_reports_not_stale_when_no_price_data_exists_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A freshly-created database has no daily_prices at all — that's
    'nothing to compare yet', not 'stale'."""
    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.is_stale is False
    assert status.daily_prices_date is None


async def test_reports_stale_when_prices_exist_but_features_never_computed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 14)],
        feature_dates=[],
    )
    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.is_stale is True
    assert status.daily_features_date is None


async def test_tolerance_is_configurable_via_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 11), date(2026, 8, 12)],
        feature_dates=[date(2026, 8, 11)],
    )
    async with session_factory() as session:
        strict_status = await check_feature_freshness(
            session, Settings(feature_freshness_max_lag_days=0)
        )

    assert strict_status.is_stale is True


async def test_ignores_inactive_symbols(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Deactivated symbols must not skew the freshness signal — both
    repository queries filter to is_active=True."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="OLDDEAD", exchange="N", instrument_token="dead1")
        )
        await session.commit()
        await PriceRepository(session).upsert_daily_many(
            symbol_row.id,
            [Candle(timestamp=datetime(2020, 1, 1), open=1, high=1, low=1, close=1, volume=1)],
        )
        await DailyFeatureRepository(session).upsert(symbol_row.id, date(2020, 1, 1), _FEATURES)
        symbol_row.is_active = False
        await session.commit()

    await _seed_symbol_with_prices_and_features(
        session_factory,
        price_dates=[date(2026, 8, 14)],
        feature_dates=[date(2026, 8, 14)],
    )

    async with session_factory() as session:
        status = await check_feature_freshness(session, Settings())

    assert status.daily_prices_date == date(2026, 8, 14)
    assert status.daily_features_date == date(2026, 8, 14)
