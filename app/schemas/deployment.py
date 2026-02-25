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
    The user can either specify a blueprint (blueprint_id) OR provide ad-hoc configuration.
    If blueprint_id is provided, other fields can still be used to override blueprint defaults.
    """
    project_id: uuid.UUID
    blueprint_id: Optional[uuid.UUID] = None
    
    # Ad-hoc / Override fields
    image_tag: Optional[str] = None
    env_vars: Optional[Dict[str, Any]] = None
    cpu_limit: Optional[str] = None
    internal_port: Optional[int] = None


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
