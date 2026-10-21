from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Without this, pydantic-settings reads real environment variables only and
    # every value below silently stays at its default.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "postgres"
    postgres_password: str = "postgres_password"
    postgres_db: str = "ticketing_platform_noai"
    postgres_port: int = 15432
    database_url: str = "postgresql+asyncpg://postgres:postgres_password@localhost:15432/ticketing_platform_noai"
    
    redis_host: str = "localhost"
    redis_port: int = 16379
    redis_url: str = "redis://localhost:16379/0"
    
    enable_cron_jobs: bool = True
    db_echo: bool = False  # SQLAlchemy statement logging; noisy, keep off by default

settings = Settings()


