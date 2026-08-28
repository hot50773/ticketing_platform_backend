from dependency_injector import containers, providers
from app.config import settings
from app.db.postgres import Database
from app.db.redis import RedisClient
from app.cache.seat import SeatLockCache
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.repositories.venue import VenueRepository
from app.services.event import EventService
from app.services.venue import VenueService

class Container(containers.DeclarativeContainer):
    """Dependency Injection Container managing project lifecycles and instances."""
    
    # Configure wiring modules for auto-inject dependencies injection
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.routers.event",
            "app.routers.venue",
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
    event_lock_cache = providers.Factory(
        SeatLockCache,
        redis_client=redis_client
    )

    # Repositories
    event_repository = providers.Factory(
        EventRepository,
        session_factory=async_session_factory
    )

    order_repository = providers.Factory(
        OrderRepository,
        session_factory=async_session_factory
    )

    venue_repository = providers.Factory(
        VenueRepository,
        session_factory=async_session_factory
    )

    venue_service = providers.Factory(
        VenueService,
        venue_repository=venue_repository
    )

    event_service = providers.Factory(
        EventService,
        event_repository=event_repository,
        order_repository=order_repository,
        event_lock_cache=event_lock_cache
    )


