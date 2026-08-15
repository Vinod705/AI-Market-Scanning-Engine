"""Integration tests for app.derivatives.oi_engine.OiEngine against an
in-memory DB, using a fake DerivativesProvider (no real network)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.derivatives.oi_engine import OiEngine
from app.models.oi_observation import OiObservation
from app.providers.base_provider import (
    DerivativesProvider,
    FuturesOiBar,
    OptionChainSnapshot,
    OptionLegSnapshot,
    ProviderSymbol,
)
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository


class _FakeDerivativesProvider(DerivativesProvider):
    """No network — returns whatever's pre-loaded per underlying symbol."""

    def __init__(self) -> None:
        self.chains: dict[str, list[OptionChainSnapshot]] = {}
        self.futures: dict[str, list[FuturesOiBar]] = {}
        self.raise_for: set[str] = set()

    async def get_option_chain(
        self, underlying_symbol: str, underlying_instrument_key: str
    ) -> list[OptionChainSnapshot]:
        if underlying_symbol in self.raise_for:
            raise RuntimeError("simulated provider failure")
        return self.chains.get(underlying_symbol, [])

    async def get_futures_oi_history(
        self, underlying_symbol: str, lookback_days: int = 5
    ) -> list[FuturesOiBar]:
        return self.futures.get(underlying_symbol, [])


def _chain_row(underlying: str) -> OptionChainSnapshot:
    return OptionChainSnapshot(
        underlying_symbol=underlying,
        underlying_instrument_key="NSE_EQ|X",
        expiry_date=date(2026, 8, 27),
        strike_price=Decimal("2400"),
        underlying_spot_price=Decimal("2380"),
        call=OptionLegSnapshot(
            instrument_key="NSE_FO|1",
            ltp=Decimal("50"),
            close_price=Decimal("40"),
            volume=1000,
            oi=Decimal("1100"),
            prev_oi=Decimal("1000"),
        ),
        put=None,
    )


async def _seed_fno_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id
        await FnoUniverseRepository(session).replace_all([symbol_id])
        await session.commit()
        return symbol_id


async def test_writes_observations_for_fno_symbol_with_chain_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_fno_symbol(session_factory, "TCS")
    provider = _FakeDerivativesProvider()
    provider.chains["TCS"] = [_chain_row("TCS")]

    engine = OiEngine(session_factory, provider)
    stats = await engine.run()

    assert stats.symbols_processed == 1
    assert stats.observations_written == 1
    assert stats.symbols_with_no_data == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        rows = (
            (await session.execute(select(OiObservation).where(OiObservation.symbol_id == symbol_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].classification == "LONG_BUILDUP"
        assert rows[0].instrument_type == "CE"


async def test_combined_chain_and_futures_write_both_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_fno_symbol(session_factory, "INFY")
    provider = _FakeDerivativesProvider()
    provider.chains["INFY"] = [_chain_row("INFY")]
    provider.futures["INFY"] = [
        FuturesOiBar(
            instrument_key="NSE_FO|2",
            expiry_date=date(2026, 9, 29),
            timestamp=datetime(2026, 8, 13),
            close=Decimal("1500"),
            volume=50000,
            open_interest=500000,
        ),
        FuturesOiBar(
            instrument_key="NSE_FO|2",
            expiry_date=date(2026, 9, 29),
            timestamp=datetime(2026, 8, 14),
            close=Decimal("1520"),
            volume=60000,
            open_interest=520000,
        ),
    ]

    engine = OiEngine(session_factory, provider)
    stats = await engine.run()

    assert stats.observations_written == 2

    async with session_factory() as session:
        rows = (
            (await session.execute(select(OiObservation).where(OiObservation.symbol_id == symbol_id)))
            .scalars()
            .all()
        )
        types = {row.instrument_type for row in rows}
        assert types == {"CE", "FUT"}


async def test_symbol_with_no_chain_or_futures_data_produces_no_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_fno_symbol(session_factory, "NODATA")
    provider = _FakeDerivativesProvider()

    engine = OiEngine(session_factory, provider)
    stats = await engine.run()

    assert stats.symbols_processed == 1
    assert stats.observations_written == 0
    assert stats.symbols_with_no_data == 1
    assert stats.error_count == 0


async def test_per_symbol_failure_is_isolated_and_does_not_abort_the_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # FnoUniverseRepository.replace_all is a full replace, not incremental —
    # both symbols must be marked F&O-eligible in one call, not two
    # sequential _seed_fno_symbol calls (the second would wipe the first).
    async with session_factory() as session:
        broken_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="BROKEN", exchange="NSE", instrument_token="NSE_EQ|BROKEN")
        )
        good_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="GOOD", exchange="NSE", instrument_token="NSE_EQ|GOOD")
        )
        await session.commit()
        good_symbol_id = good_row.id
        await FnoUniverseRepository(session).replace_all([broken_row.id, good_row.id])
        await session.commit()

    provider = _FakeDerivativesProvider()
    provider.raise_for.add("BROKEN")
    provider.chains["GOOD"] = [_chain_row("GOOD")]

    engine = OiEngine(session_factory, provider)
    stats = await engine.run()

    assert stats.symbols_processed == 2
    assert stats.error_count == 1
    assert stats.observations_written == 1

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OiObservation).where(OiObservation.symbol_id == good_symbol_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_only_fno_universe_symbols_are_processed_not_the_full_listed_universe(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A symbol with no derivative contracts (not in FnoUniverse) must
    never be assumed to have OI — OiEngine only iterates FnoUniverse."""
    async with session_factory() as session:
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOFNO", exchange="NSE", instrument_token="NSE_EQ|NOFNO")
        )
        await session.commit()
    # No FnoUniverse row for NOFNO -> never passed to the provider at all.

    provider = _FakeDerivativesProvider()
    engine = OiEngine(session_factory, provider)
    stats = await engine.run()

    assert stats.symbols_processed == 0
