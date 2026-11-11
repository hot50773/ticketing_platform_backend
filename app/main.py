from contextlib import asynccontextmanager
import asyncio
import logging
import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
from app.jobs.scheduler import start_scheduler
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

    # Start background tasks
    scheduler = start_scheduler(container)

    # The API only enqueues; `taskiq worker` / `taskiq scheduler` consume.
    await broker.startup()
    await schedule_source.startup()

    yield  # Runs the application

    print("Cancelling background tasks...")
    if scheduler:
        scheduler.shutdown()

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


# 3. Map domain exceptions to HTTP status codes in one place
@app.exception_handler(EntityNotFoundError)
async def _handle_not_found(_request: Request, exc: EntityNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AuthenticationError)
async def _handle_unauthenticated(_request: Request, exc: AuthenticationError):
    return JSONResponse(
        status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"}
    )


@app.exception_handler(PermissionDeniedError)
async def _handle_forbidden(_request: Request, exc: PermissionDeniedError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(TooManyLoginAttemptsError)
async def _handle_too_many_logins(_request: Request, exc: TooManyLoginAttemptsError):
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(InvalidEventTimeError)
async def _handle_invalid_time(_request: Request, exc: InvalidEventTimeError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(EntityConflictError)
async def _handle_conflict(_request: Request, exc: EntityConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(BusinessRuleViolationError)
async def _handle_rule_violation(_request: Request, exc: BusinessRuleViolationError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# 4. Register Routers
for module in (auth, event, order, venue):
    app.include_router(module.router, prefix="/api/v1")
# Every route only managers may call lives under /admin.
for module in (auth, manager, event, order, venue):
    app.include_router(module.admin_router, prefix="/api/v1")

@app.get("/")
async def root():
    """Redirect root access to API docs."""
    return {"message": "Concert Ticket Locking Service is running. Please access /docs."}

