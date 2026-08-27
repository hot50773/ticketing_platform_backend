from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.containers import Container
from app.routers.event import router as events_router
from app.jobs.scheduler import start_scheduler

import asyncio
import logging
import sys


logger = logging.getLogger(__name__)



# Instantiate container and wire it at module level so injection decorators resolve correctly
container = Container()
container.wire(modules=["app.routers.event", "app.main"])

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

# 3. Register Routers
app.include_router(events_router, prefix="/api/v1")

@app.get("/")
async def root():
    """Redirect root access to API docs."""
    return {"message": "Concert Ticket Locking Service is running. Please access /docs."}
