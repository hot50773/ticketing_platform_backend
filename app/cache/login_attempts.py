from app.db.redis import RedisClient


class LoginAttemptLimiter:
    """Counts failed manager log-ins per (client IP, email) in a fixed window.

    Keyed on the pair rather than the email alone, so an attacker cannot lock a
    victim out by failing on purpose from elsewhere.
    """

    KEY = "login:failures:manager:{client_ip}:{email}"

    def __init__(self, redis_client: RedisClient, max_failures: int, window_seconds: int):
        self._redis_client = redis_client
        self._max_failures = max_failures
        self._window_seconds = window_seconds

    async def failure_count(self, client_ip: str, email: str) -> int:
        return int(await self._redis_client.get(self._key(client_ip, email)) or 0)

    def is_over_limit(self, failures: int) -> bool:
        return failures >= self._max_failures

    async def record_failure(self, client_ip: str, email: str) -> None:
        key = self._key(client_ip, email)
        async with self._redis_client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # nx: the window starts at the first failure and is not pushed back by later ones
            pipe.expire(key, self._window_seconds, nx=True)
            await pipe.execute()

    async def reset(self, client_ip: str, email: str) -> None:
        await self._redis_client.delete(self._key(client_ip, email))

    def _key(self, client_ip: str, email: str) -> str:
        return self.KEY.format(client_ip=client_ip, email=email)
