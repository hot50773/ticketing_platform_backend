import redis.asyncio as aioredis
from app.config import settings

class RedisClient:
    """Class-based Redis client wrapper implementing the Singleton pattern."""
    def __init__(self):
        self._client = aioredis.from_url(settings.redis_url, decode_responses=True)



    def __getattr__(self, name: str):
        """Forwards all attribute lookups and calls directly to the wrapped Redis client."""
        return getattr(self._client, name)

    async def shutdown(self):
        """Closes the underlying Redis connection."""
        await self._client.aclose()