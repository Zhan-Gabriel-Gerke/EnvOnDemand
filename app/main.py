from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.api.endpoints import deployments, blueprints, projects, auth, volumes
from app.api.deps import get_current_user
from pathlib import Path

# Define the base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="EnvOnDemand API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the router for deployment management
app.include_router(deployments.router, prefix="/api", tags=["Deployments"], dependencies=[Depends(get_current_user)])
app.include_router(blueprints.router, prefix="/api/blueprints", tags=["Blueprints"], dependencies=[Depends(get_current_user)])
app.include_router(projects.router, prefix="/api/projects", tags=["Projects"], dependencies=[Depends(get_current_user)])
app.include_router(volumes.router, prefix="/api", tags=["Volumes"], dependencies=[Depends(get_current_user)])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])

@app.get("/")
def read_index():
    return FileResponse(
        STATIC_DIR / "index.html", 
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

# Mount static files - must be last as it's a catch-all
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
