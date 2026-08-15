"""Persistence for `momentum_states` (current position) and
`momentum_state_transitions` (append-only log)."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.momentum_state import MomentumStateRecord
from app.models.momentum_state_transition import MomentumStateTransition
from app.momentum.momentum_models import MomentumState, StateTransition


class MomentumStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, symbol_id: int) -> MomentumStateRecord | None:
        stmt = select(MomentumStateRecord).where(MomentumStateRecord.symbol_id == symbol_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def apply_transition(
        self, symbol_id: int, transition: StateTransition
    ) -> tuple[MomentumStateRecord, int]:
        """Updates the current-state pointer and appends one transition-log
        row, both in the caller's existing session/transaction — the two
        writes are never split across separate commits, so the log and
        the current pointer can never disagree about the last move.
        Returns the transition-log row's own id alongside the current-state
        record — Phase 16's operational-validation observations anchor on
        this id so a live trigger's tracked evidence/score always traces
        back to the exact append-only log row that produced it."""
        transition_row = MomentumStateTransition(
            symbol_id=symbol_id,
            from_state=transition.from_state.value if transition.from_state else None,
            to_state=transition.to_state.value,
            timestamp=transition.timestamp,
            reason=transition.reason,
            score=transition.score,
            evidence=transition.evidence,
        )
        self._session.add(transition_row)

        record = await self.get_current(symbol_id)
        if record is None:
            record = MomentumStateRecord(symbol_id=symbol_id, entered_at=transition.timestamp)
            self._session.add(record)

        record.state = transition.to_state.value
        record.score = transition.score
        record.reason = transition.reason
        record.evidence = transition.evidence
        record.entered_at = transition.timestamp

        await self._session.flush()
        return record, transition_row.id

    @staticmethod
    def state_of(record: MomentumStateRecord | None) -> MomentumState | None:
        return MomentumState(record.state) if record is not None else None

    async def list_by_states(
        self, states: Iterable[MomentumState], limit: int = 20
    ) -> list[MomentumStateRecord]:
        """Current position for every symbol sitting in one of `states` —
        a read-only view over `momentum_states`, never touching the
        engine that put a symbol there. Used for the dashboard's "Top
        Momentum Candidates" section, typically called with
        `ALERT_WORTHY_STATES`."""
        stmt = (
            select(MomentumStateRecord)
            .where(MomentumStateRecord.state.in_([s.value for s in states]))
            .order_by(MomentumStateRecord.score.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_recent_transitions(self, limit: int = 50) -> list[MomentumStateTransition]:
        """Most recent rows from the append-only transition log, across
        all symbols — the dashboard's "Trigger History" section."""
        stmt = (
            select(MomentumStateTransition)
            .order_by(MomentumStateTransition.timestamp.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_transitions_since(
        self, since: datetime, states: Iterable[MomentumState]
    ) -> int:
        """Used by the digest job to summarize momentum activity over its
        lookback window without loading every row into Python."""
        stmt = select(func.count(MomentumStateTransition.id)).where(
            MomentumStateTransition.timestamp >= since,
            MomentumStateTransition.to_state.in_([s.value for s in states]),
        )
        return (await self._session.execute(stmt)).scalar_one()
