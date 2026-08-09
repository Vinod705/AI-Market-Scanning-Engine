"""Redis connection for the session store — same lazy-singleton pattern
`app/database/session.py` uses for the SQLAlchemy engine."""

from redis.asyncio import Redis

from app.config.settings import get_settings

settings = get_settings()

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def check_redis_connection() -> bool:
    return bool(await redis_client.ping())


async def dispose_redis() -> None:
    await redis_client.aclose()
