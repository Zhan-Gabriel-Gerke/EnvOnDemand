import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from pydantic import BaseModel, ConfigDict

from app.models.models import DeploymentStatus


# --- Deployment Schemas ---

# Command Schema (Input)
class DeploymentCreate(BaseModel):
    """
    Schema for creating a deployment (Command).
    The user only specifies the project and the blueprint to use.
    All other configuration is snapshotted by the backend from the blueprint.
    """
    project_id: uuid.UUID
    blueprint_id: uuid.UUID


# Query Schema (Output)
class DeploymentRead(BaseModel):
    """
    Schema for reading a deployment (Query).
    Returns the full state of the deployment, including the snapshotted configuration
    and system-managed fields.
    """
    id: uuid.UUID
    project_id: uuid.UUID
    blueprint_id: Optional[uuid.UUID]  # Can be null if the original blueprint was deleted
    status: DeploymentStatus
    container_id: Optional[str]
    created_at: datetime

    # Snapshotted configuration
    image_tag: str
    env_vars: Dict[str, Any]
    cpu_limit: Optional[str]
    internal_port: int
    external_port: Optional[int]

    model_config = ConfigDict(from_attributes=True)
