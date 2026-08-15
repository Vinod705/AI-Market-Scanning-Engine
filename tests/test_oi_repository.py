"""Tests for app.repositories.oi_repository.OiObservationRepository."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.derivatives.derivatives_models import BuildupClassification, InstrumentType, OiReading
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository
from app.repositories.oi_repository import OiObservationRepository


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_FO|{symbol}")
        )
        await session.commit()
        return row.id


def _reading(
    *,
    instrument_type: InstrumentType,
    classification: BuildupClassification,
    oi_change_pct: Decimal | None,
    observed_at: datetime,
) -> OiReading:
    return OiReading(
        underlying_symbol="X",
        instrument_key="NSE_FO|X",
        instrument_type=instrument_type,
        strike_price=None,
        expiry_date=date(2026, 1, 29),
        observed_at=observed_at,
        price=Decimal("100"),
        prev_price=Decimal("95"),
        price_change_pct=Decimal("5.26"),
        volume=1000,
        oi=Decimal("50000"),
        prev_oi=Decimal("40000"),
        oi_change=Decimal("10000"),
        oi_change_pct=oi_change_pct,
        classification=classification,
    )


async def test_list_latest_buildups_excludes_neutral(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    neutral_id = await _seed_symbol(session_factory, "NEUTRALSTOCK")
    buildup_id = await _seed_symbol(session_factory, "BUILDUPSTOCK")

    async with session_factory() as session:
        repo = OiObservationRepository(session)
        await repo.insert(
            neutral_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.NEUTRAL,
                oi_change_pct=Decimal("1.0"),
                observed_at=now,
            ),
        )
        await repo.insert(
            buildup_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.LONG_BUILDUP,
                oi_change_pct=Decimal("25.0"),
                observed_at=now,
            ),
        )
        await session.commit()

        results = await repo.list_latest_buildups(limit=20)

    symbols = {r.symbol_id for r in results}
    assert buildup_id in symbols
    assert neutral_id not in symbols


async def test_list_latest_buildups_uses_latest_reading_per_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An older buildup reading must not shadow a newer non-buildup
    (or differently-classified) reading for the same symbol."""
    now = datetime.now(UTC)
    symbol_id = await _seed_symbol(session_factory, "COVERSTOCK")

    async with session_factory() as session:
        repo = OiObservationRepository(session)
        await repo.insert(
            symbol_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.LONG_BUILDUP,
                oi_change_pct=Decimal("10.0"),
                observed_at=now - timedelta(hours=1),
            ),
        )
        await repo.insert(
            symbol_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.SHORT_COVERING,
                oi_change_pct=Decimal("-15.0"),
                observed_at=now,
            ),
        )
        await session.commit()

        results = await repo.list_latest_buildups(limit=20)

    matching = [r for r in results if r.symbol_id == symbol_id]
    assert len(matching) == 1
    assert matching[0].classification == BuildupClassification.SHORT_COVERING.value


async def test_list_latest_buildups_orders_by_magnitude_of_move(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    small_id = await _seed_symbol(session_factory, "SMALLMOVE")
    big_id = await _seed_symbol(session_factory, "BIGMOVE")

    async with session_factory() as session:
        repo = OiObservationRepository(session)
        await repo.insert(
            small_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.LONG_BUILDUP,
                oi_change_pct=Decimal("2.0"),
                observed_at=now,
            ),
        )
        await repo.insert(
            big_id,
            _reading(
                instrument_type=InstrumentType.FUTURES,
                classification=BuildupClassification.SHORT_BUILDUP,
                oi_change_pct=Decimal("-40.0"),
                observed_at=now,
            ),
        )
        await session.commit()

        results = await repo.list_latest_buildups(limit=20)

    assert results[0].symbol_id == big_id


async def test_list_latest_buildups_ignores_option_observations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    symbol_id = await _seed_symbol(session_factory, "OPTONLY")

    async with session_factory() as session:
        repo = OiObservationRepository(session)
        await repo.insert(
            symbol_id,
            _reading(
                instrument_type=InstrumentType.CALL,
                classification=BuildupClassification.LONG_BUILDUP,
                oi_change_pct=Decimal("30.0"),
                observed_at=now,
            ),
        )
        await session.commit()

        results = await repo.list_latest_buildups(limit=20)

    assert all(r.symbol_id != symbol_id for r in results)
