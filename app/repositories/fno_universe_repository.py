"""Repository layer for `fno_universe` — the F&O-eligible symbol set."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fno_universe import FnoUniverse


class FnoUniverseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_all(self, symbol_ids: list[int]) -> int:
        """Full replace, not incremental upsert: F&O eligibility is a
        current classification (a symbol can lose its F&O contracts), so a
        stale row left behind by a previous refresh would be actively
        wrong, not just outdated."""
        await self._session.execute(delete(FnoUniverse))
        for symbol_id in symbol_ids:
            self._session.add(FnoUniverse(symbol_id=symbol_id))
        await self._session.flush()
        return len(symbol_ids)

    async def list_symbol_ids(self) -> list[int]:
        stmt = select(FnoUniverse.symbol_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def count(self) -> int:
        return len(await self.list_symbol_ids())
