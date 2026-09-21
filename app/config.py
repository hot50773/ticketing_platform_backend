from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Without this the .env file is never read at all: pydantic-settings only
    # looks at real environment variables by default, so every value below
    # silently stayed at its hard-coded default and the app talked to
    # autocomplete_db - a leftover from another project - instead of the
    # database .env names.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "postgres"
    postgres_password: str = "postgres_password"
    postgres_db: str = "autocomplete_db"
    postgres_port: int = 5432
    database_url: str = "postgresql+asyncpg://postgres:postgres_password@localhost:5432/autocomplete_db"
    
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"
    
    enable_cron_jobs: bool = True
    db_echo: bool = False  # SQLAlchemy statement logging; noisy, keep off by default

settings = Settings()


