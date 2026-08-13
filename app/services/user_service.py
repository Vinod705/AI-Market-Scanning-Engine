"""User account business logic backing `/auth/*` and `/admin/users/*`.

Every write here goes through `UserRepository` (never raw SQL) and every
password ever touches only `app.auth.passwords` (hash on write, verify
on read) — nothing in this file, or anywhere upstream of it, ever sees
or logs a plaintext password after the moment it's generated/submitted.
"""

from dataclasses import dataclass

from app.auth.models import UserRole, UserStatus
from app.auth.passwords import generate_temporary_password, hash_password, verify_password
from app.auth.session_store import SessionStore
from app.core.time import utc_now
from app.models.user import User
from app.repositories.user_repository import UserRepository


@dataclass
class CreatedUser:
    user: User
    temporary_password: str


class UserService:
    def __init__(self, repo: UserRepository, session_store: SessionStore) -> None:
        self._repo = repo
        self._sessions = session_store

    async def authenticate(self, email: str, password: str) -> User | None:
        """Returns the user only if the email exists, the account is
        ACTIVE, and the password matches — never distinguishes "no such
        user" from "wrong password" in what it returns, so a caller can't
        use response differences to enumerate valid emails."""
        user = await self._repo.get_by_email(email)
        if user is None or user.status != UserStatus.ACTIVE.value:
            return None
        if not verify_password(password, user.password_hash):
            return None
        await self._repo.record_login(user, when=utc_now())
        return user

    async def change_password(
        self, user: User, *, current_password: str, new_password: str
    ) -> bool:
        if not verify_password(current_password, user.password_hash):
            return False
        await self._repo.set_password(user, hash_password(new_password), must_change_password=False)
        return True

    async def list_users(self) -> list[User]:
        return await self._repo.list_all()

    async def create_viewer(self, *, email: str, name: str, role: UserRole) -> CreatedUser:
        temp_password = generate_temporary_password()
        user = await self._repo.create(
            email=email,
            name=name,
            password_hash=hash_password(temp_password),
            role=role.value,
            must_change_password=True,
        )
        return CreatedUser(user=user, temporary_password=temp_password)

    async def disable(self, user: User) -> None:
        await self._repo.set_status(user, UserStatus.DISABLED.value)
        await self._sessions.delete_all_for_user(user.id)

    async def enable(self, user: User) -> None:
        await self._repo.set_status(user, UserStatus.ACTIVE.value)

    async def reset_password(self, user: User) -> str:
        temp_password = generate_temporary_password()
        await self._repo.set_password(user, hash_password(temp_password), must_change_password=True)
        await self._sessions.delete_all_for_user(user.id)
        return temp_password

    async def change_role(self, user: User, role: UserRole) -> None:
        await self._repo.set_role(user, role.value)

    async def delete(self, user: User) -> None:
        await self._sessions.delete_all_for_user(user.id)
        await self._repo.delete(user)
