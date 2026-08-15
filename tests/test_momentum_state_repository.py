"""Tests for the Phase 15 read methods on
app.repositories.momentum_state_repository.MomentumStateRepository —
list_by_states/list_recent_transitions/count_transitions_since. The
write path (apply_transition) is already covered by
tests/test_momentum_engine.py."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.momentum.momentum_models import ALERT_WORTHY_STATES, MomentumState, StateTransition
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        return row.id


async def _apply(
    session_factory: async_sessionmaker[AsyncSession],
    symbol_id: int,
    symbol: str,
    *,
    to_state: MomentumState,
    score: float,
    timestamp: datetime,
) -> None:
    async with session_factory() as session:
        await MomentumStateRepository(session).apply_transition(
            symbol_id,
            StateTransition(
                symbol=symbol,
                from_state=None,
                to_state=to_state,
                timestamp=timestamp,
                reason="test",
                score=score,
            ),
        )
        await session.commit()


async def test_list_by_states_filters_and_orders_by_score_desc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    watch_id = await _seed_symbol(session_factory, "WATCHER")
    low_trigger_id = await _seed_symbol(session_factory, "LOWTRIGGER")
    high_trigger_id = await _seed_symbol(session_factory, "HIGHTRIGGER")

    await _apply(session_factory, watch_id, "WATCHER", to_state=MomentumState.WATCH, score=40.0, timestamp=now)
    await _apply(session_factory, low_trigger_id, "LOWTRIGGER", to_state=MomentumState.TRIGGERED, score=65.0, timestamp=now)
    await _apply(session_factory, high_trigger_id, "HIGHTRIGGER", to_state=MomentumState.TRIGGERED, score=95.0, timestamp=now)

    async with session_factory() as session:
        results = await MomentumStateRepository(session).list_by_states(
            ALERT_WORTHY_STATES, limit=20
        )

    symbol_ids = [r.symbol_id for r in results]
    assert watch_id not in symbol_ids
    assert symbol_ids == [high_trigger_id, low_trigger_id]


async def test_list_recent_transitions_orders_newest_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    symbol_id = await _seed_symbol(session_factory, "PROGRESSING")

    await _apply(session_factory, symbol_id, "PROGRESSING", to_state=MomentumState.WATCH, score=40.0, timestamp=now - timedelta(minutes=10))
    await _apply(session_factory, symbol_id, "PROGRESSING", to_state=MomentumState.ACTIVATING, score=65.0, timestamp=now)

    async with session_factory() as session:
        results = await MomentumStateRepository(session).list_recent_transitions(limit=20)

    assert [r.to_state for r in results[:2]] == ["ACTIVATING", "WATCH"]


async def test_count_transitions_since_filters_by_time_and_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    old_id = await _seed_symbol(session_factory, "OLDTRIGGER")
    recent_id = await _seed_symbol(session_factory, "RECENTTRIGGER")
    recent_watch_id = await _seed_symbol(session_factory, "RECENTWATCH")

    await _apply(session_factory, old_id, "OLDTRIGGER", to_state=MomentumState.TRIGGERED, score=90.0, timestamp=now - timedelta(hours=20))
    await _apply(session_factory, recent_id, "RECENTTRIGGER", to_state=MomentumState.TRIGGERED, score=90.0, timestamp=now - timedelta(hours=1))
    await _apply(session_factory, recent_watch_id, "RECENTWATCH", to_state=MomentumState.WATCH, score=40.0, timestamp=now - timedelta(hours=1))

    async with session_factory() as session:
        count = await MomentumStateRepository(session).count_transitions_since(
            now - timedelta(hours=9), ALERT_WORTHY_STATES
        )

    assert count == 1
