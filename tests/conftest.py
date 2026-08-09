"""Shared pytest fixtures."""

import os

# Must be set before `app.main` (and therefore `get_settings()`) is ever
# imported: the session cookie's `Secure` flag defaults to True (this
# project requires HTTPS in production — see app/config/settings.py), and
# httpx's cookie jar correctly refuses to re-send a Secure cookie over the
# plain http://test base_url the test client uses. Real HTTPS behavior is
# covered separately by test_auth.py's cookie-flags assertion and by the
# live VM verification, not by round-tripping cookies here.
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.dependencies import get_current_user  # noqa: E402
from app.auth.models import AuthenticatedUser, UserRole  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.main import app  # noqa: E402

# Import models so Base.metadata is populated before create_all runs.
from app.models import *  # noqa: F401,F403

# Every pre-Phase-6 test hits protected routes (/candidates, /alerts,
# /decisions/*) with no concept of logging in — rather than rewriting all
# of them, the shared `client` fixture overrides `get_current_user` with a
# fake, always-authenticated admin. The actual auth mechanism (login,
# sessions, roles, disabled accounts, ...) is exercised for real in
# tests/test_auth.py, which builds its own client without this override.
_TEST_USER = AuthenticatedUser(
    id=0, email="test@example.com", name="Test User", role=UserRole.ADMIN
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_current_user] = lambda: _TEST_USER
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An isolated in-memory SQLite database per test, schema pre-created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()
