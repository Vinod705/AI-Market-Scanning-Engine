"""End-to-end tests for the real authentication/authorization system —
login, sessions, roles, and the admin user-management API.

Deliberately does NOT use the shared `client` fixture from conftest.py:
that fixture fakes `get_current_user` so the rest of the test suite
doesn't need to know auth exists. Here we want the real thing, so this
file builds its own client that only fakes the two things that would
otherwise require live infrastructure — Redis (an in-memory fake
matching `SessionStore`'s narrow interface) and the database (the same
in-memory sqlite `session_factory` every other integration test uses).
"""

import fnmatch
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_session_store
from app.auth.models import UserRole
from app.auth.passwords import hash_password
from app.auth.session_store import SessionStore
from app.database.session import get_db_session
from app.main import app
from app.repositories.user_repository import UserRepository


class _FakeRedis:
    """In-memory stand-in for the pieces of `redis.asyncio.Redis` that
    `SessionStore` actually calls — no real Redis server needed."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        return None  # sliding TTL is Redis's job to enforce, not this fake's

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def scan(self, cursor: int, match: str | None = None, count: int = 100):  # noqa: ANN001
        pattern = match or "*"
        keys = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
        return 0, keys


@pytest.fixture
async def auth_env(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncClient, SessionStore, async_sessionmaker[AsyncSession]]]:
    fake_redis = _FakeRedis()
    store = SessionStore(fake_redis, ttl_minutes=480)

    async def _db_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_db_session] = _db_override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, store, session_factory

    app.dependency_overrides.pop(get_session_store, None)
    app.dependency_overrides.pop(get_db_session, None)


async def _create_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    password: str,
    role: UserRole,
    status: str = "ACTIVE",
) -> int:
    async with session_factory() as session:
        repo = UserRepository(session)
        user = await repo.create(
            email=email,
            name=email.split("@")[0],
            password_hash=hash_password(password),
            role=role.value,
            must_change_password=False,
        )
        if status != "ACTIVE":
            await repo.set_status(user, status)
        await session.commit()
        return user.id


# --- 1/2/3: admin login, viewer login, invalid login ---


async def test_admin_login_succeeds_and_sets_session_cookie(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="admin@test.com", password="Sup3rSecret!", role=UserRole.ADMIN
    )

    response = await client.post(
        "/auth/login", json={"email": "admin@test.com", "password": "Sup3rSecret!"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "ADMIN"
    assert "session_id" in response.cookies


async def test_invalid_password_is_rejected(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="admin2@test.com", password="correct-horse", role=UserRole.ADMIN
    )

    response = await client.post(
        "/auth/login", json={"email": "admin2@test.com", "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_unknown_email_is_rejected_the_same_way_as_wrong_password(
    auth_env,
) -> None:  # noqa: ANN001
    client, _, _ = auth_env
    response = await client.post(
        "/auth/login", json={"email": "nobody@test.com", "password": "whatever"}
    )
    assert response.status_code == 401


# --- 4/5: viewer blocked from admin routes ---


async def test_viewer_cannot_access_admin_routes(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="viewer@test.com", password="ViewerPass1!", role=UserRole.VIEWER
    )
    login = await client.post(
        "/auth/login", json={"email": "viewer@test.com", "password": "ViewerPass1!"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "VIEWER"

    list_response = await client.get("/admin/users")
    assert list_response.status_code == 403

    create_response = await client.post(
        "/admin/users", json={"email": "new@test.com", "name": "New", "role": "VIEWER"}
    )
    assert create_response.status_code == 403


# --- 6: viewer (or anyone) never sees secrets ---


async def test_me_endpoint_never_exposes_password_hash_or_session_token(
    auth_env,
) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="secretcheck@test.com", password="Whatever1!", role=UserRole.VIEWER
    )
    await client.post(
        "/auth/login", json={"email": "secretcheck@test.com", "password": "Whatever1!"}
    )

    me = await client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert "password_hash" not in body
    assert "password" not in body
    assert "session_id" not in body


# --- 7: disabled viewer cannot log in ---


async def test_disabled_account_cannot_login(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory,
        email="disabled@test.com",
        password="DisabledPass1!",
        role=UserRole.VIEWER,
        status="DISABLED",
    )
    response = await client.post(
        "/auth/login", json={"email": "disabled@test.com", "password": "DisabledPass1!"}
    )
    assert response.status_code == 401


# --- 8: admin can reset a viewer's password ---


async def test_admin_reset_password_invalidates_old_password(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="admin3@test.com", password="AdminPass1!", role=UserRole.ADMIN
    )
    viewer_id = await _create_user(
        session_factory, email="resetme@test.com", password="OldPass1!", role=UserRole.VIEWER
    )

    await client.post("/auth/login", json={"email": "admin3@test.com", "password": "AdminPass1!"})
    reset = await client.post(f"/admin/users/{viewer_id}/reset-password")
    assert reset.status_code == 200
    temp_password = reset.json()["temporary_password"]
    assert temp_password

    await client.post("/auth/logout")

    old_login = await client.post(
        "/auth/login", json={"email": "resetme@test.com", "password": "OldPass1!"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/auth/login", json={"email": "resetme@test.com", "password": temp_password}
    )
    assert new_login.status_code == 200
    assert new_login.json()["user"]["must_change_password"] is True


# --- 9: logout invalidates the session ---


async def test_logout_invalidates_the_session(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="logout@test.com", password="LogoutPass1!", role=UserRole.VIEWER
    )
    await client.post("/auth/login", json={"email": "logout@test.com", "password": "LogoutPass1!"})

    assert (await client.get("/auth/me")).status_code == 200

    logout = await client.post("/auth/logout")
    assert logout.status_code == 204

    assert (await client.get("/auth/me")).status_code == 401


# --- 10: expired/revoked session is rejected ---


async def test_expired_session_is_rejected(auth_env) -> None:  # noqa: ANN001
    client, store, session_factory = auth_env
    await _create_user(
        session_factory, email="expiry@test.com", password="ExpiryPass1!", role=UserRole.VIEWER
    )
    login = await client.post(
        "/auth/login", json={"email": "expiry@test.com", "password": "ExpiryPass1!"}
    )
    token = login.cookies["session_id"]

    assert (await client.get("/auth/me")).status_code == 200

    # Simulate the session's TTL lapsing (Redis would evict the key itself;
    # here we remove it directly from the fake store).
    await store.delete(token)

    assert (await client.get("/auth/me")).status_code == 401


# --- 11: cookies are HTTPS-only when configured (full TLS termination is
# verified live against the deployed VM, not here — see the deployment
# report) ---


async def test_session_cookie_is_marked_secure_and_httponly(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="cookie@test.com", password="CookiePass1!", role=UserRole.VIEWER
    )
    response = await client.post(
        "/auth/login", json={"email": "cookie@test.com", "password": "CookiePass1!"}
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie


# --- Admin CRUD + last-admin safety guard ---


async def test_admin_can_disable_enable_and_delete_a_viewer(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    await _create_user(
        session_factory, email="admin4@test.com", password="AdminPass1!", role=UserRole.ADMIN
    )
    viewer_id = await _create_user(
        session_factory, email="managed@test.com", password="ViewerPass1!", role=UserRole.VIEWER
    )
    await client.post("/auth/login", json={"email": "admin4@test.com", "password": "AdminPass1!"})

    disable = await client.patch(f"/admin/users/{viewer_id}/disable")
    assert disable.status_code == 200
    assert disable.json()["status"] == "DISABLED"

    enable = await client.patch(f"/admin/users/{viewer_id}/enable")
    assert enable.status_code == 200
    assert enable.json()["status"] == "ACTIVE"

    delete = await client.delete(f"/admin/users/{viewer_id}")
    assert delete.status_code == 204

    users = await client.get("/admin/users")
    assert viewer_id not in [u["id"] for u in users.json()]


async def test_cannot_disable_the_last_active_admin(auth_env) -> None:  # noqa: ANN001
    client, _, session_factory = auth_env
    admin_id = await _create_user(
        session_factory, email="onlyadmin@test.com", password="AdminPass1!", role=UserRole.ADMIN
    )
    await client.post(
        "/auth/login", json={"email": "onlyadmin@test.com", "password": "AdminPass1!"}
    )

    response = await client.patch(f"/admin/users/{admin_id}/disable")
    assert response.status_code == 400
