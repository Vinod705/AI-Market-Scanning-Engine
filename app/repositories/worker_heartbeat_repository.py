"""Repository for `worker_heartbeats` — see app.models.worker_heartbeat's
module docstring for why this exists (a cross-process worker-liveness
signal for the sanity checker)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.worker_heartbeat import WorkerHeartbeat


class WorkerHeartbeatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ping_run(self, worker_name: str, *, detail: str | None = None) -> None:
        """Records an attempted cycle — moves `last_run_at` regardless of
        outcome. Call this even for a legitimate no-op (e.g. market
        closed) so the sanity checker can tell "alive, nothing to do"
        apart from "stopped entirely"."""
        row = await self._get_or_create(worker_name)
        row.last_run_at = utc_now()
        if detail is not None:
            row.detail = detail
        await self._session.flush()

    async def ping_success(self, worker_name: str, *, detail: str | None = None) -> None:
        """Records a cycle that completed without raising — moves both
        `last_run_at` and `last_success_at`."""
        now = utc_now()
        row = await self._get_or_create(worker_name)
        row.last_run_at = now
        row.last_success_at = now
        if detail is not None:
            row.detail = detail
        await self._session.flush()

    async def get_all(self) -> dict[str, WorkerHeartbeat]:
        rows = (await self._session.execute(select(WorkerHeartbeat))).scalars().all()
        return {row.worker_name: row for row in rows}

    async def get(self, worker_name: str) -> WorkerHeartbeat | None:
        return await self._session.get(WorkerHeartbeat, worker_name)

    async def _get_or_create(self, worker_name: str) -> WorkerHeartbeat:
        row = await self._session.get(WorkerHeartbeat, worker_name)
        if row is None:
            row = WorkerHeartbeat(worker_name=worker_name)
            self._session.add(row)
        return row
