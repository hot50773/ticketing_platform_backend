import importlib
import pkgutil

from dependency_injector import containers, providers

from app.cache.event_layout import EventLayoutCache
from app.db.postgres import Database
from app.db.redis import RedisClient
from app.repositories.event import EventRepository
from app.repositories.venue import VenueRepository
from app.services.event import EventService
from app.services.event_layout import EventLayoutService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from app.services.venue import VenueService


def _get_modules(package_name: str) -> list[str]:
    package = importlib.import_module(package_name)
    modules = []

    for _, module_name, is_pkg in pkgutil.walk_packages(path=package.__path__):
        if is_pkg:
            modules.extend(_get_modules(f"{package_name}.{module_name}"))
        else:
            modules.append(f"{package_name}.{module_name}")

    return modules


class Container(containers.DeclarativeContainer):
    """Dependency Injection Container managing project lifecycles and instances."""

    # Configure wiring modules for auto-inject dependencies injection
    router_modules = _get_modules("app.routers")
    wiring_config = containers.WiringConfiguration(modules=["app.main"] + router_modules)

    # Database connection manager registered as a singleton
    db = providers.Singleton(Database)

    # db_session_factory resolves to the session_maker attribute of the Database singleton
    async_session_factory = db.provided.async_session_maker

    # Redis connection only. The seat-status cache layer is intentionally absent:
    # the Redis seat bitmap is to be implemented separately.
    redis_client = providers.Singleton(RedisClient)

    # Repositories
    event_repository = providers.Factory(
        EventRepository,
        session_factory=async_session_factory
    )

    venue_repository = providers.Factory(
        VenueRepository,
        session_factory=async_session_factory
    )

    # Cache
    event_layout_cache = providers.Factory(EventLayoutCache, redis_client=redis_client)

    # Domain rule validators (pure, no I/O)
    event_rule_validator = providers.Factory(EventRuleValidator)

    # ORM -> schema translation, kept out of the services
    event_schema_mapper = providers.Factory(EventSchemaMapper)

    # Services
    venue_service = providers.Factory(
        VenueService,
        venue_repository=venue_repository
    )

    event_service = providers.Factory(
        EventService,
        event_repository=event_repository,
        event_rule_validator=event_rule_validator,
        event_schema_mapper=event_schema_mapper,
        event_layout_cache=event_layout_cache
    )

    event_layout_service = providers.Factory(
        EventLayoutService,
        event_repository=event_repository,
        event_rule_validator=event_rule_validator,
        event_schema_mapper=event_schema_mapper
    )
