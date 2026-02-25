from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.api.endpoints import deployments, blueprints, projects

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
app.include_router(deployments.router, prefix="/api", tags=["Deployments"])
app.include_router(blueprints.router, prefix="/api/blueprints", tags=["Blueprints"])
app.include_router(projects.router, prefix="/api/projects", tags=["Projects"])

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

# Mount static files - must be last as it's a catch-all
app.mount("/static", StaticFiles(directory="static"), name="static")
