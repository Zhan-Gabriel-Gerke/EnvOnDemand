from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.endpoints import deployments

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager to handle application startup and shutdown events.
    """
    print("Application startup...")
    # Here you could add logic to connect to databases, initialize resources, etc.
    
    yield  # The application runs while the context manager is active
    
    print("Application shutdown...")
    # Here you could add logic to close connections, clean up resources, etc.

app = FastAPI(
    title="EnvOnDemand API",
    description="API for on-demand environment deployments.",
    version="0.1.0",
    lifespan=lifespan  # Use the modern lifespan event handler
)

@app.get("/", tags=["Root"])
async def read_root():
    """A simple root endpoint to confirm the API is running."""
    return {"message": "Welcome to EnvOnDemand API"}

# Include the router for deployment management
app.include_router(deployments.router, prefix="/api", tags=["Deployments"])
