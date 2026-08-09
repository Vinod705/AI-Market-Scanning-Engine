"""Server-side session storage in Redis.

Deliberately not a JWT: a real logout and true server-side session
expiration/revocation (both explicitly required) need a session record
the server can delete on demand, not just a signed token the client
happens to discard. Redis is already provisioned in docker-compose for
exactly this kind of ephemeral, TTL'd data — this is its first real use
in the project.

The session token itself (a high-entropy random string, never the
database's numeric user id) is what's stored in the browser cookie;
Redis maps it to `{user_id, role}` with a sliding TTL. Nothing about a
user's password ever passes through here.
"""

import json
import secrets
from typing import TypedDict

from redis.asyncio import Redis

_SESSION_KEY_PREFIX = "session:"


class SessionData(TypedDict):
    user_id: int
    role: str


class SessionStore:
    def __init__(self, redis_client: Redis, ttl_minutes: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_minutes * 60

    async def create(self, *, user_id: int, role: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"user_id": user_id, "role": role})
        await self._redis.setex(_SESSION_KEY_PREFIX + token, self._ttl_seconds, payload)
        return token

    async def get(self, token: str) -> SessionData | None:
        raw = await self._redis.get(_SESSION_KEY_PREFIX + token)
        if raw is None:
            return None
        data = json.loads(raw)
        return SessionData(user_id=data["user_id"], role=data["role"])

    async def touch(self, token: str) -> None:
        """Sliding expiration: activity resets the TTL. No-ops silently if
        the session no longer exists (e.g. it just expired mid-request)."""
        await self._redis.expire(_SESSION_KEY_PREFIX + token, self._ttl_seconds)

    async def delete(self, token: str) -> None:
        await self._redis.delete(_SESSION_KEY_PREFIX + token)

    async def delete_all_for_user(self, user_id: int) -> None:
        """Best-effort revocation of every active session for a user —
        used when an account is disabled or deleted, so access doesn't
        linger until the session's natural TTL expiry."""
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=_SESSION_KEY_PREFIX + "*", count=100
            )
            for key in keys:
                raw = await self._redis.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                if data.get("user_id") == user_id:
                    await self._redis.delete(key)
            if cursor == 0:
                break
