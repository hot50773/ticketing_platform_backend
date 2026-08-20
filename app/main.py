from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.containers import Container
from app.routers.seat import router as autocomplete_router
from app.routers.nickname import router as nickname_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manages application startup and shutdown lifecycles."""
    _app.container = Container()
    
    # Clean up connection resources
    db = _app.container.db()
    redis_client = _app.container.redis_client()

    print("Closing PostgreSQL engine and sessions...")
    await db.close_all_sessions()

    print("Closing Redis cache connection pool...")
    await redis_client.shutdown()

# 2. Instantiate FastAPI Application with lifespan
app = FastAPI(
    title="Welcome to FastAPI Backend Service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs"
)

# Bind the _container instance to app state for lifecycle access if needed


# 3. Register Routers
app.include_router(autocomplete_router)
app.include_router(nickname_router)

@app.get("/")
async def root():
    """Redirect root access to API docs."""
    return {"message": "Class-based Redis Autocomplete Service is running. Please access /docs."}
