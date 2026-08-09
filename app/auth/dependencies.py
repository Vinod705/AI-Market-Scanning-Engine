"""FastAPI auth dependencies: `get_current_user` (any active account) and
`require_admin` (ADMIN role only) — every protected route in
`app/api/candidates.py`, `app/api/alerts.py`, and `app/api/admin_users.py`
depends on one of these rather than checking cookies/roles itself.

`/health` deliberately does NOT depend on these — it's the Docker
healthcheck target (`docker-compose.yml`), which has no session cookie
and conventionally stays unauthenticated like any liveness probe.
"""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthenticatedUser, UserRole
from app.auth.redis_client import redis_client
from app.auth.session_store import SessionStore
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.repositories.user_repository import UserRepository

# Fixed at process start from Settings, same pattern as the module-level
# `engine`/`redis_client` singletons elsewhere in this project — imported
# by app/api/auth.py too, so the cookie this dependency reads and the
# cookie the login/logout endpoints set can never drift apart.
SESSION_COOKIE_NAME: str = get_settings().session_cookie_name

_UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def get_session_store() -> SessionStore:
    settings = get_settings()
    return SessionStore(redis_client, settings.session_ttl_minutes)


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Store = Annotated[SessionStore, Depends(get_session_store)]


async def get_current_user(
    session: DbSession,
    store: Store,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthenticatedUser:
    if not session_id:
        raise _UNAUTHORIZED

    data = await store.get(session_id)
    if data is None:
        raise _UNAUTHORIZED

    user = await UserRepository(session).get_by_id(data["user_id"])
    if user is None or user.status != "ACTIVE":
        # Deleted or disabled after the session was issued — the session
        # row itself may still have time left on its TTL, but the account
        # no longer authorizes anything.
        await store.delete(session_id)
        raise _UNAUTHORIZED

    await store.touch(session_id)  # sliding expiration on activity
    return AuthenticatedUser(id=user.id, email=user.email, name=user.name, role=UserRole(user.role))


async def require_admin(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
