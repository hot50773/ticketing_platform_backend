"""Pins the whole test session to docker-compose.test.yaml's isolated stack.

ENV_FILE has to be set before app.config is imported - `settings` is built at
import time. Root conftest runs before any other conftest or test module.
"""
import os

os.environ.setdefault("ENV_FILE", ".env.test")

from app.config import ENV_FILE, settings  # noqa: E402  (must follow the line above)

# Asserted rather than read from the env file: a check that reads its
# expectation from the thing it checks asserts nothing.
TEST_DATABASE_NAME = "ticketing_test_db"
TEST_REDIS_PORT = "56379"

_MISCONFIGURED = (
    "Refusing to run: the test session is not pointed at the isolated stack.\n"
    "  ENV_FILE      = {env_file}\n"
    "  database_url  = {database_url}\n"
    "  redis_url     = {redis_url}\n"
    "Expected database {db!r} and redis port {port} "
    "(see docker-compose.test.yaml).\n"
    "The e2e fixtures truncate every table and flush Redis - running them "
    "against anything else destroys real data.\n"
    "A DATABASE_URL or REDIS_URL exported in your shell overrides .env.test; "
    "unset it, then:\n"
    "  docker compose --env-file .env.test -f docker-compose.test.yaml up -d\n"
    "  ENV_FILE=.env.test uv run alembic upgrade head"
)


def _assert_isolated_stack() -> None:
    """A real environment variable outranks an env_file in pydantic-settings,
    so an exported DATABASE_URL silently wins over .env.test."""
    if TEST_DATABASE_NAME in settings.database_url and TEST_REDIS_PORT in settings.redis_url:
        return
    raise RuntimeError(
        _MISCONFIGURED.format(
            env_file=ENV_FILE,
            database_url=settings.database_url,
            redis_url=settings.redis_url,
            db=TEST_DATABASE_NAME,
            port=TEST_REDIS_PORT,
        )
    )


_assert_isolated_stack()
