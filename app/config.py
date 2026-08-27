from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_user: str = "postgres"
    postgres_password: str = "postgres_password"
    postgres_db: str = "autocomplete_db"
    postgres_port: int = 5432
    database_url: str = "postgresql+asyncpg://postgres:postgres_password@localhost:5432/autocomplete_db"
    
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"
    
    enable_cron_jobs: bool = True

settings = Settings()


