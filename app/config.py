import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# tests/conftest.py sets this before importing app.config: `settings` below is
# built at import time, so a later switch is too late. alembic shares it.
ENV_FILE = os.getenv("ENV_FILE", ".env")


class Settings(BaseSettings):
    # Without this, pydantic-settings reads real environment variables only and
    # every value below silently stays at its default.
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    postgres_user: str = "postgres"
    postgres_password: str = "postgres_password"
    postgres_db: str = "ticketing_platform_noai"
    postgres_port: int = 15432
    database_url: str = "postgresql+asyncpg://postgres:postgres_password@localhost:15432/ticketing_platform_noai"
    
    redis_host: str = "localhost"
    redis_port: int = 16379
    redis_url: str = "redis://localhost:16379/0"
    
    # No default: a secret baked into the code signs tokens anyone can forge
    # HS256 is only as strong as its key; RFC 7518 asks for at least 256 bits
    jwt_secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 60
    # Empty turns Google sign-in off
    google_client_id: str = ""
    login_max_failures: int = 5
    login_failure_window_seconds: int = 900

    db_echo: bool = False  # SQLAlchemy statement logging; noisy, keep off by default

settings = Settings()


