import importlib
import pkgutil

from dependency_injector import containers, providers

from app.cache.event_detail import EventDetailCache
from app.cache.seat_layout import SeatLayoutCache
from app.cache.event_list import EventListCache
from app.cache.login_attempts import LoginAttemptLimiter
from app.config import settings
from app.db.postgres import Database
from app.db.redis import RedisClient
from app.libs.security import AccessTokenCodec, GoogleIdTokenVerifier, PasswordHasher
from app.queue.delayed.process_expired_order import OrderExpirationQueue
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.repositories.customer import CustomerRepository
from app.repositories.manager import ManagerRepository
from app.repositories.venue import VenueRepository
from app.services.auth import AuthService
from app.services.calculators.order import OrderCalculator
from app.services.event import EventService
from app.services.seat_layout import SeatLayoutService
from app.services.mappers.customer import CustomerSchemaMapper
from app.services.mappers.manager import ManagerSchemaMapper
from app.services.order import OrderService, UnimplementedPaymentGateway
from app.services.mappers import EventSchemaMapper
from app.services.mappers.order import OrderSchemaMapper
from app.services.customer import CustomerService
from app.services.manager import ManagerService
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
    customer_repository = providers.Factory(
        CustomerRepository,
        session_factory=async_session_factory
    )

    manager_repository = providers.Factory(
        ManagerRepository,
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
    seat_layout_cache = providers.Factory(SeatLayoutCache, redis_client=redis_client)
    event_list_cache = providers.Factory(EventListCache, redis_client=redis_client)
    event_detail_cache = providers.Factory(EventDetailCache, redis_client=redis_client)
    login_attempt_limiter = providers.Factory(
        LoginAttemptLimiter,
        redis_client=redis_client,
        max_failures=settings.login_max_failures,
        window_seconds=settings.login_failure_window_seconds,
    )

    # Domain rule validators (pure, no I/O)
    event_rule_validator = providers.Factory(EventRuleValidator)
    order_validator = providers.Factory(OrderValidator)

    # Pricing and deadlines, kept out of the services (also pure, no I/O)
    order_calculator = providers.Factory(OrderCalculator)

    # Credentials
    password_hasher = providers.Singleton(PasswordHasher)
    access_token_codec = providers.Singleton(
        AccessTokenCodec,
        secret_key=settings.jwt_secret_key,
        expire_minutes=settings.access_token_expire_minutes,
    )
    google_id_token_verifier = providers.Singleton(
        GoogleIdTokenVerifier, client_id=settings.google_client_id
    )

    # ORM -> schema translation, kept out of the services
    customer_schema_mapper = providers.Factory(CustomerSchemaMapper)
    manager_schema_mapper = providers.Factory(ManagerSchemaMapper)
    event_schema_mapper = providers.Factory(EventSchemaMapper)
    order_schema_mapper = providers.Factory(OrderSchemaMapper)

    # Services
    customer_service = providers.Factory(
        CustomerService,
        customer_repository=customer_repository,
        customer_schema_mapper=customer_schema_mapper,
    )
    manager_service = providers.Factory(
        ManagerService,
        manager_repository=manager_repository,
        manager_schema_mapper=manager_schema_mapper,
        password_hasher=password_hasher,
    )
    auth_service = providers.Factory(
        AuthService,
        customer_repository=customer_repository,
        manager_repository=manager_repository,
        password_hasher=password_hasher,
        token_codec=access_token_codec,
        google_verifier=google_id_token_verifier,
        login_attempt_limiter=login_attempt_limiter,
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
        seat_cache=seat_layout_cache,
        event_list_cache=event_list_cache,
        event_detail_cache=event_detail_cache,
    )

    seat_layout_service = providers.Factory(
        SeatLayoutService,
        event_repository=event_repository,
        event_rule_validator=event_rule_validator,
        event_schema_mapper=event_schema_mapper,
        event_detail_cache=event_detail_cache,
    )

    # The payment provider is a placeholder that raises: binding a fake success
    # here would let an order reach CONFIRMED with no money behind it. Swap in
    # the real gateway when POST /orders/{order_id}/pay goes live.
    payment_gateway = providers.Factory(UnimplementedPaymentGateway)

    order_expiration_queue = providers.Factory(OrderExpirationQueue)

    order_service = providers.Factory(
        OrderService,
        order_repository=order_repository,
        order_validator=order_validator,
        order_calculator=order_calculator,
        order_schema_mapper=order_schema_mapper,
        payment_gateway=payment_gateway,
        seat_layout_cache=seat_layout_cache,
        order_expiration_scheduler=order_expiration_queue,
    )

