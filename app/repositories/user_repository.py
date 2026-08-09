"""Repository layer for `users` — dashboard login accounts."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[User]:
        stmt = select(User).order_by(User.created_at.asc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        role: str,
        must_change_password: bool,
    ) -> User:
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            role=role,
            status="ACTIVE",
            must_change_password=must_change_password,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_status(self, user: User, status: str) -> None:
        user.status = status
        await self._session.flush()

    async def set_password(
        self, user: User, password_hash: str, *, must_change_password: bool
    ) -> None:
        user.password_hash = password_hash
        user.must_change_password = must_change_password
        await self._session.flush()

    async def set_role(self, user: User, role: str) -> None:
        user.role = role
        await self._session.flush()

    async def record_login(self, user: User, *, when: datetime) -> None:
        user.last_login_at = when
        await self._session.flush()

    async def delete(self, user: User) -> None:
        await self._session.delete(user)
        await self._session.flush()
