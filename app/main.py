from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.containers import Container
from app.routers.seat import router as events_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manages application startup and shutdown lifecycles."""
    _app.container = Container()
    
    # Get connections
    db = _app.container.db()
    redis_client = _app.container.redis_client()

    yield  # Runs the application

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
