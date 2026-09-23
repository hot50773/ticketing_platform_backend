from contextlib import asynccontextmanager
import asyncio
import logging
import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.containers import Container
from app.exceptions import (
    BusinessRuleViolationError,
    EntityConflictError,
    EntityNotFoundError,
    InvalidEventTimeError,
)
from app.routers.event import router as events_router
from app.routers.order import router as orders_router
from app.routers.venue import router as venue_router
from app.jobs.scheduler import start_scheduler

logger = logging.getLogger(__name__)

# Instantiate container and wire it at module level so injection decorators resolve correctly
container = Container()
container.wire(
    modules=["app.routers.event", "app.routers.order", "app.routers.venue", "app.main"]
)

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

    yield  # Runs the application

    print("Cancelling background tasks...")
    if scheduler:
        scheduler.shutdown()

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

# 3. Map domain exceptions to HTTP status codes in one place
@app.exception_handler(EntityNotFoundError)
async def _handle_not_found(_request: Request, exc: EntityNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


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
app.include_router(events_router, prefix="/api/v1")
app.include_router(orders_router, prefix="/api/v1")
app.include_router(venue_router, prefix="/api/v1")

@app.get("/")
async def root():
    """Redirect root access to API docs."""
    return {"message": "Concert Ticket Locking Service is running. Please access /docs."}

