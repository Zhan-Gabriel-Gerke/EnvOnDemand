import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.models.models import DeploymentStatus, ContainerStatus


# --- Container Schemas ---

class DeploymentContainerCreate(BaseModel):
    """Schema for a single container inside a deployment."""
    name: str = Field(..., description="Name of the container, e.g., 'db' or 'backend'")
    image: str = Field(..., description="Docker image tag, e.g., 'postgres:15-alpine'")
    role: str = Field(..., description="Role of the container, e.g., 'database', 'api'")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables for the container")
    ports: Optional[Dict[int, int]] = Field(default=None, description="Port mappings: host_port -> container_port")

class DeploymentContainerRead(BaseModel):
    id: uuid.UUID
    container_id: Optional[str]
    name: str
    image: str
    role: str
    status: ContainerStatus

    model_config = ConfigDict(from_attributes=True)


# --- Deployment Schemas ---

class DeploymentCreate(BaseModel):
    """
    Schema for creating a deployment (Command).
    Expects a network name and a list of containers to deploy.
    """
    network_name: Optional[str] = Field(None, description="Optional custom network name")
    containers: List[DeploymentContainerCreate] = Field(..., description="List of containers to deploy in this environment")

    @field_validator('containers')
    @classmethod
    def check_containers_not_empty(cls, v: List[DeploymentContainerCreate]) -> List[DeploymentContainerCreate]:
        if not v:
            raise ValueError('A deployment must contain at least one container.')
        return v


class DeploymentRead(BaseModel):
    """
    Schema for reading a deployment (Query).
    Returns the full state of the deployment and its containers.
    """
    id: uuid.UUID
    user_id: uuid.UUID
    network_name: Optional[str]
    status: DeploymentStatus
    created_at: datetime
    containers: List[DeploymentContainerRead] = []

    model_config = ConfigDict(from_attributes=True)
