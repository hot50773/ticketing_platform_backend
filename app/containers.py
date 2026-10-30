import importlib
import pkgutil

from dependency_injector import containers, providers

from app.cache.event_layout import EventLayoutCache
from app.db.postgres import Database
from app.db.redis import RedisClient
from app.queue.delayed.process_expired_order import OrderExpirationQueue
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.repositories.user import UserRepository
from app.repositories.venue import VenueRepository
from app.services.calculators.order import OrderCalculator
from app.services.event import EventService
from app.services.event_layout import EventLayoutService
from app.services.mappers.user import UserSchemaMapper
from app.services.order import OrderService, UnimplementedPaymentGateway
from app.services.mappers import EventSchemaMapper
from app.services.mappers.order import OrderSchemaMapper
from app.services.user import UserService
from app.services.validators import EventRuleValidator
from app.services.validators.order import OrderValidator
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
    user_repository = providers.Factory(
        UserRepository,
        session_factory=async_session_factory
    )

    event_repository = providers.Factory(
        EventRepository,
        session_factory=async_session_factory
    )

    venue_repository = providers.Factory(
        VenueRepository,
        session_factory=async_session_factory
    )

    order_repository = providers.Factory(
        OrderRepository,
        session_factory=async_session_factory
    )

    # Cache
    event_layout_cache = providers.Factory(EventLayoutCache, redis_client=redis_client)

    # Domain rule validators (pure, no I/O)
    event_rule_validator = providers.Factory(EventRuleValidator)
    order_validator = providers.Factory(OrderValidator)

    # Pricing and deadlines, kept out of the services (also pure, no I/O)
    order_calculator = providers.Factory(OrderCalculator)

    # ORM -> schema translation, kept out of the services
    user_schema_mapper = providers.Factory(UserSchemaMapper)
    event_schema_mapper = providers.Factory(EventSchemaMapper)
    order_schema_mapper = providers.Factory(OrderSchemaMapper)

    # Services
    user_service = providers.Factory(
        UserService,
        user_repository=user_repository,
        user_schema_mapper=user_schema_mapper
    )
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

    # The payment provider is a placeholder that raises: binding a fake success
    # here would let an order reach CONFIRMED with no money behind it. Swap in
    # the real gateway when POST /orders/{order_id}/pay goes live.
    payment_gateway = providers.Factory(UnimplementedPaymentGateway)

    order_expiration_queue = providers.Factory(OrderExpirationQueue)

    order_service = providers.Factory(
        OrderService,
        order_repository=order_repository,
        event_repository=event_repository,
        order_validator=order_validator,
        order_calculator=order_calculator,
        order_schema_mapper=order_schema_mapper,
        payment_gateway=payment_gateway,
        event_layout_cache=event_layout_cache,
        order_expiration_scheduler=order_expiration_queue,
    )

