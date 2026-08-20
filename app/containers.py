from dependency_injector import containers, providers
from app.config import settings
from app.db.postgres import Database
from app.db.redis import RedisClient
from app.cache.seat import SeatLockCache
from app.repositories.seat import SeatRepository
from app.services.seat import SeatService

class Container(containers.DeclarativeContainer):
    """Dependency Injection Container managing project lifecycles and instances."""
    
    # Configure wiring modules for auto-inject dependencies injection
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.routers.seat",
            "app.main"
        ]
    )

    # Database connection manager registered as a singleton
    db = providers.Singleton(Database)
    
    # db_session_factory resolves to the session_maker attribute of the Database singleton
    async_session_factory = db.provided.async_session_maker

    # Redis Client (Singleton wrapper)x
    redis_client = providers.Singleton(RedisClient)

    # Cache Clients
    seat_lock_cache = providers.Factory(
        SeatLockCache,
        redis_client=redis_client
    )

    # Repositories
    seat_repository = providers.Factory(
        SeatRepository,
        session_factory=async_session_factory
    )

    seat_service = providers.Factory(
        SeatService,
        seat_repository=seat_repository,
        seat_lock_cache=seat_lock_cache
    )
