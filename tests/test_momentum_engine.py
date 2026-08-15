"""Integration tests for app.momentum.momentum_engine.MomentumStateEngine —
persistence + real AlertManager/AlertQueue reuse, against an in-memory DB.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.models.momentum_state_transition import MomentumStateTransition
from app.momentum.momentum_engine import MomentumStateEngine
from app.momentum.momentum_models import MomentumState
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository

_SETTINGS = Settings(decision_min_alert_score=90.0)  # type: ignore[call-arg]
_START = datetime(2026, 8, 15, 9, 30)


def _engine(session_factory: async_sessionmaker[AsyncSession]) -> MomentumStateEngine:
    alert_manager = AlertManager(session_factory, _SETTINGS, AlertQueue())
    return MomentumStateEngine(session_factory, _SETTINGS, alert_manager)


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        return row.id


async def test_unknown_symbol_produces_no_transition_and_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = _engine(session_factory)
    result = await engine.evaluate("NOSUCHSYMBOL", 95.0, {})
    assert result.transition is None
    assert result.alert_id is None


async def test_first_evaluation_creates_state_and_transition_log_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "SETUPSYM")
    engine = _engine(session_factory)

    result = await engine.evaluate("SETUPSYM", 10.0, {"technical_score": 10.0}, now=_START)

    assert result.transition is not None
    assert result.transition.to_state == MomentumState.SETUP
    assert result.alert_id is None  # SETUP is not alert-worthy

    async with session_factory() as session:
        record = await MomentumStateRepository(session).get_current(symbol_id)
        assert record is not None
        assert record.state == "SETUP"

        transitions = (
            (
                await session.execute(
                    select(MomentumStateTransition).where(
                        MomentumStateTransition.symbol_id == symbol_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(transitions) == 1
        assert transitions[0].from_state is None
        assert transitions[0].to_state == "SETUP"
        assert transitions[0].reason
        assert transitions[0].score == 10.0


async def test_repeated_identical_score_produces_no_new_transition_or_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "STABLESYM")
    engine = _engine(session_factory)

    first = await engine.evaluate("STABLESYM", 40.0, {}, now=_START)
    assert first.transition is not None
    assert first.transition.to_state == MomentumState.WATCH

    second = await engine.evaluate("STABLESYM", 40.0, {}, now=_START + timedelta(minutes=1))
    assert second.transition is None
    assert second.alert_id is None

    async with session_factory() as session:
        transitions = (
            (
                await session.execute(
                    select(MomentumStateTransition).where(
                        MomentumStateTransition.symbol_id == symbol_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(transitions) == 1  # still just the one from the first evaluation


async def test_full_progression_alerts_only_on_triggered_and_confirmed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SETUP -> WATCH -> ACTIVATING -> TRIGGERED -> CONFIRMED across 5
    evaluations of a genuinely developing (ascending) score — each
    evaluation lands one band higher, one step at a time. Alerts fire
    only for the two alert-worthy states, never for SETUP/WATCH/
    ACTIVATING. (Settings here: trigger=90, band_low=30, band_mid=60.)"""
    await _seed_symbol(session_factory, "PROGRESSSYM")
    engine = _engine(session_factory)
    scores = [10.0, 40.0, 70.0, 95.0, 95.0]

    t = _START
    results = []
    for score in scores:
        results.append(await engine.evaluate("PROGRESSSYM", score, {"score": score}, now=t))
        t += timedelta(minutes=1)

    states = [r.transition.to_state if r.transition else None for r in results]
    assert states == [
        MomentumState.SETUP,
        MomentumState.WATCH,
        MomentumState.ACTIVATING,
        MomentumState.TRIGGERED,
        MomentumState.CONFIRMED,
    ]
    alert_flags = [r.alert_id is not None for r in results]
    assert alert_flags == [False, False, False, True, True]


async def test_alert_manager_dedup_is_reused_not_reimplemented(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The alert actually created by a TRIGGERED transition is a real row
    in the same `alerts` table AlertManager/AlertDeduplicator already
    manage — proof this reuses the existing repository, not a parallel
    one."""
    await _seed_symbol(session_factory, "ALERTSYM")
    engine = _engine(session_factory)
    t = _START
    for score in [10.0, 40.0, 70.0]:  # SETUP -> WATCH -> ACTIVATING
        await engine.evaluate("ALERTSYM", score, {}, now=t)
        t += timedelta(minutes=1)
    triggered_result = await engine.evaluate("ALERTSYM", 95.0, {}, now=t)  # -> TRIGGERED

    assert triggered_result.alert_id is not None
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(triggered_result.alert_id)
        assert alert is not None
        assert alert.signal_type == "TRIGGERED"
        assert alert.scanner_name == "momentum_state_v1"


async def test_exhausted_transition_after_confirmed_is_not_alert_worthy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "FADESYM")
    engine = _engine(session_factory)
    t = _START
    for score in [10.0, 40.0, 70.0, 95.0, 95.0]:  # -> SETUP, WATCH, ACTIVATING, TRIGGERED, CONFIRMED
        await engine.evaluate("FADESYM", score, {}, now=t)
        t += timedelta(minutes=1)

    faded = await engine.evaluate("FADESYM", 20.0, {}, now=t)
    assert faded.transition is not None
    assert faded.transition.to_state == MomentumState.EXHAUSTED
    assert faded.alert_id is None
