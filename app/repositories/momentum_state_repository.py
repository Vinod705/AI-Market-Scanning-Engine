"""Persistence for `momentum_states` (current position) and
`momentum_state_transitions` (append-only log)."""

from sqlalchemy import select
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
    ) -> MomentumStateRecord:
        """Updates the current-state pointer and appends one transition-log
        row, both in the caller's existing session/transaction — the two
        writes are never split across separate commits, so the log and
        the current pointer can never disagree about the last move."""
        self._session.add(
            MomentumStateTransition(
                symbol_id=symbol_id,
                from_state=transition.from_state.value if transition.from_state else None,
                to_state=transition.to_state.value,
                timestamp=transition.timestamp,
                reason=transition.reason,
                score=transition.score,
                evidence=transition.evidence,
            )
        )

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
        return record

    @staticmethod
    def state_of(record: MomentumStateRecord | None) -> MomentumState | None:
        return MomentumState(record.state) if record is not None else None
