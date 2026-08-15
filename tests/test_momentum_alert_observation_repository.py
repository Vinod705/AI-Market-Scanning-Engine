"""Tests for app.repositories.momentum_alert_observation_repository.
MomentumAlertObservationRepository — Phase 16's operational-validation
record persistence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.momentum.momentum_models import MomentumState, StateTransition
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
    NewObservation,
)
from app.repositories.momentum_state_repository import MomentumStateRepository


async def _seed_transition(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, *, timestamp: datetime
) -> tuple[int, int]:
    """Returns (symbol_id, transition_id) for a real TRIGGERED transition —
    matches what MomentumPipelineCoordinator actually has on hand when it
    writes an observation."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        _record, transition_id = await MomentumStateRepository(session).apply_transition(
            symbol_row.id,
            StateTransition(
                symbol=symbol,
                from_state=None,
                to_state=MomentumState.TRIGGERED,
                timestamp=timestamp,
                reason="score cleared trigger band",
                score=87.0,
            ),
        )
        await session.commit()
        return symbol_row.id, transition_id


def _observation(symbol_id: int, transition_id: int, *, trigger_at: datetime, **overrides: object) -> NewObservation:
    defaults: dict[str, object] = dict(
        transition_id=transition_id,
        symbol_id=symbol_id,
        alert_id=None,
        momentum_state="TRIGGERED",
        trigger_at=trigger_at,
        signal_score=Decimal("87.00"),
        signal_confidence=Decimal("70.00"),
        as_of_data_at=trigger_at - timedelta(seconds=10),
        data_age_seconds=10.0,
        is_stale=False,
        price_at_trigger=Decimal("100.50"),
    )
    defaults.update(overrides)
    return NewObservation(**defaults)  # type: ignore[arg-type]


async def test_insert_then_list_recent_round_trips_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    symbol_id, transition_id = await _seed_transition(session_factory, "TRIGSYM", timestamp=now)

    async with session_factory() as session:
        repo = MomentumAlertObservationRepository(session)
        await repo.insert(_observation(symbol_id, transition_id, trigger_at=now))
        await session.commit()

        rows = await repo.list_recent(limit=10)

    assert len(rows) == 1
    assert rows[0].symbol_id == symbol_id
    assert rows[0].momentum_state == "TRIGGERED"
    assert rows[0].signal_score == Decimal("87.00")
    assert rows[0].price_at_trigger == Decimal("100.50")
    assert rows[0].is_stale is False


async def test_list_recent_orders_newest_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    older_symbol_id, older_transition_id = await _seed_transition(
        session_factory, "OLDER", timestamp=now - timedelta(minutes=10)
    )
    newer_symbol_id, newer_transition_id = await _seed_transition(
        session_factory, "NEWER", timestamp=now
    )

    async with session_factory() as session:
        repo = MomentumAlertObservationRepository(session)
        await repo.insert(
            _observation(older_symbol_id, older_transition_id, trigger_at=now - timedelta(minutes=10))
        )
        await repo.insert(_observation(newer_symbol_id, newer_transition_id, trigger_at=now))
        await session.commit()

        rows = await repo.list_recent(limit=10)

    assert [r.symbol_id for r in rows] == [newer_symbol_id, older_symbol_id]


async def test_list_awaiting_15m_only_returns_rows_old_enough_and_unfilled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    old_id, old_tid = await _seed_transition(session_factory, "OLDENOUGH", timestamp=now - timedelta(minutes=20))
    recent_id, recent_tid = await _seed_transition(session_factory, "TOORECENT", timestamp=now - timedelta(minutes=5))

    async with session_factory() as session:
        repo = MomentumAlertObservationRepository(session)
        await repo.insert(_observation(old_id, old_tid, trigger_at=now - timedelta(minutes=20)))
        await repo.insert(_observation(recent_id, recent_tid, trigger_at=now - timedelta(minutes=5)))
        await session.commit()

        awaiting = await repo.list_awaiting_15m(now - timedelta(minutes=15))

    assert [r.symbol_id for r in awaiting] == [old_id]


async def test_list_awaiting_15m_excludes_already_filled_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    symbol_id, transition_id = await _seed_transition(session_factory, "ALREADYFILLED", timestamp=now - timedelta(minutes=20))

    async with session_factory() as session:
        repo = MomentumAlertObservationRepository(session)
        row = await repo.insert(_observation(symbol_id, transition_id, trigger_at=now - timedelta(minutes=20)))
        row.price_after_15m = Decimal("101.00")
        await session.commit()

        awaiting = await repo.list_awaiting_15m(now - timedelta(minutes=15))

    assert awaiting == []


async def test_list_awaiting_1h_and_1d_use_their_own_cutoffs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    hour_old_id, hour_old_tid = await _seed_transition(session_factory, "HOUROLD", timestamp=now - timedelta(hours=2))
    day_old_id, day_old_tid = await _seed_transition(session_factory, "DAYOLD", timestamp=now - timedelta(days=2))

    async with session_factory() as session:
        repo = MomentumAlertObservationRepository(session)
        await repo.insert(_observation(hour_old_id, hour_old_tid, trigger_at=now - timedelta(hours=2)))
        await repo.insert(_observation(day_old_id, day_old_tid, trigger_at=now - timedelta(days=2)))
        await session.commit()

        awaiting_1h = await repo.list_awaiting_1h(now - timedelta(hours=1))
        awaiting_1d = await repo.list_awaiting_1d(now - timedelta(days=1))

    assert {r.symbol_id for r in awaiting_1h} == {hour_old_id, day_old_id}
    assert {r.symbol_id for r in awaiting_1d} == {day_old_id}
