from contextlib import asynccontextmanager
import asyncio
import logging
import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from app.config import settings
from app.containers import Container
from app.exceptions import (
    AuthenticationError,
    BusinessRuleViolationError,
    EntityConflictError,
    EntityNotFoundError,
    InvalidEventTimeError,
    PermissionDeniedError,
    TooManyLoginAttemptsError,
)
from app.routers import auth, event, manager, order, venue
from app.workers import broker, schedule_source

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("app.audit")

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

# Instantiate container and wire it at module level so injection decorators resolve correctly
container = Container()

IS_TESTING = "pytest" in sys.modules


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manages application startup and shutdown lifecycles."""
    _app.container = container
    
    # Get connections
    db = container.db()
    redis_client = container.redis_client()

    # The API only enqueues; `taskiq worker` / `taskiq scheduler` consume.
    await broker.startup()
    await schedule_source.startup()

    yield  # Runs the application

    print("Closing taskiq broker...")
    await schedule_source.shutdown()
    await broker.shutdown()

    print("Closing PostgreSQL engine and sessions...")
    await db.close_all_sessions()

    print("Closing Redis cache connection pool...")
    await redis_client.shutdown()


# 2. Instantiate FastAPI Application with lifespan
app = FastAPI(
    title="Concert Ticket Locking API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs"
)

@app.middleware("http")
async def _audit_manager_writes(request: Request, call_next):
    response = await call_next(request)
    manager = getattr(request.state, "manager", None)
    if manager is not None and request.method not in READ_ONLY_METHODS:
        audit_logger.info(
            "manager=%s role=%s %s %s -> %s",
            manager.id, manager.role, request.method, request.url.path, response.status_code,
        )
    return response


# Added last so it runs first: the login rate limit keys on request.client, which
# behind a load balancer is otherwise the proxy shared by every client.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.forwarded_allow_ips)


# 3. Map domain exceptions to HTTP status codes in one place
# Starlette picks the handler by the exception's MRO, so subclasses listed
# here (InvalidEventTimeError) win over their base (BusinessRuleViolationError).
DOMAIN_ERROR_STATUS = {
    EntityNotFoundError: 404,
    PermissionDeniedError: 403,
    TooManyLoginAttemptsError: 429,
    InvalidEventTimeError: 422,
    EntityConflictError: 409,
    BusinessRuleViolationError: 409,
}


def _json_error(status_code: int):
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})
    return handle


for error_type, status_code in DOMAIN_ERROR_STATUS.items():
    app.add_exception_handler(error_type, _json_error(status_code))


@app.exception_handler(AuthenticationError)
async def _handle_unauthenticated(_request: Request, exc: AuthenticationError):
    return JSONResponse(
        status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"}
    )


# 4. Register Routers
for module in (auth, event, order, venue):
    app.include_router(module.customer_router, prefix="/api/v1")
# Every route only managers may call lives under /admin.
for module in (auth, manager, event, order, venue):
    app.include_router(module.manager_router, prefix="/api/v1")

@app.get("/")
async def root():
    """Redirect root access to API docs."""
    return {"message": "Concert Ticket Locking Service is running. Please access /docs."}

