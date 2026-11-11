"""LoginAttemptLimiter: failures count per (client IP, email), never per IP alone.

Behind a reverse proxy every client shares one IP, so an IP-only key would lock
every manager out after one person's typos.
"""
from app.cache.login_attempts import LoginAttemptLimiter

PROXY_IP = "10.0.0.1"


class FakeRedis:
    """Just the commands the limiter uses; expiry is not simulated."""

    def __init__(self):
        self.values: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._keys: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def incr(self, key: str) -> None:
        self._keys.append(key)

    def expire(self, key: str, seconds: int, nx: bool = False) -> None:
        pass

    async def execute(self) -> None:
        for key in self._keys:
            self._redis.values[key] = self._redis.values.get(key, 0) + 1


def a_limiter(max_failures: int = 3) -> LoginAttemptLimiter:
    return LoginAttemptLimiter(FakeRedis(), max_failures=max_failures, window_seconds=900)


async def blocked(limiter: LoginAttemptLimiter, client_ip: str, email: str) -> bool:
    return limiter.is_over_limit(await limiter.failure_count(client_ip, email))


async def fail(limiter: LoginAttemptLimiter, times: int, client_ip: str, email: str) -> None:
    for _ in range(times):
        await limiter.record_failure(client_ip, email)


async def test_the_same_ip_and_email_is_blocked_after_max_failures():
    limiter = a_limiter()

    await fail(limiter, 2, PROXY_IP, "mia@example.com")
    assert not await blocked(limiter, PROXY_IP, "mia@example.com")

    await fail(limiter, 1, PROXY_IP, "mia@example.com")
    assert await blocked(limiter, PROXY_IP, "mia@example.com")


async def test_other_accounts_behind_the_same_proxy_ip_are_not_blocked():
    limiter = a_limiter()

    await fail(limiter, 3, PROXY_IP, "mia@example.com")

    assert not await blocked(limiter, PROXY_IP, "root@example.com")


async def test_failures_on_different_accounts_do_not_add_up():
    limiter = a_limiter()

    await fail(limiter, 2, PROXY_IP, "mia@example.com")
    await fail(limiter, 2, PROXY_IP, "root@example.com")

    assert not await blocked(limiter, PROXY_IP, "mia@example.com")
    assert not await blocked(limiter, PROXY_IP, "root@example.com")


async def test_the_same_account_from_another_ip_is_not_blocked():
    limiter = a_limiter()

    await fail(limiter, 3, PROXY_IP, "mia@example.com")

    assert not await blocked(limiter, "203.0.113.7", "mia@example.com")


async def test_reset_clears_only_that_ip_and_email():
    limiter = a_limiter()
    await fail(limiter, 3, PROXY_IP, "mia@example.com")
    await fail(limiter, 3, PROXY_IP, "root@example.com")

    await limiter.reset(PROXY_IP, "mia@example.com")

    assert not await blocked(limiter, PROXY_IP, "mia@example.com")
    assert await blocked(limiter, PROXY_IP, "root@example.com")
