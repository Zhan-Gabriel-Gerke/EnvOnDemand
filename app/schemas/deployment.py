import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.models import DeploymentStatus, ContainerStatus


# --- Container Schemas ---

class DeploymentContainerCreate(BaseModel):
    """
    Schema for a single container inside a deployment.

    Exactly one of ``image`` or ``git_url`` must be provided:
    - ``image``: pull a pre-built Docker image from a registry.
    - ``git_url``: clone the repository and build the image on-the-fly.
    """
    name: str = Field(..., description="Container name, e.g. 'backend' or 'db'")
    role: str = Field(..., description="Role of the container, e.g. 'api', 'database'")

    # Source — exactly one must be set (validated below)
    image: Optional[str] = Field(
        default=None,
        description="Docker image tag, e.g. 'postgres:15-alpine'. Mutually exclusive with git_url.",
    )
    git_url: Optional[str] = Field(
        default=None,
        description="Git repository URL to clone and build. Mutually exclusive with image.",
    )

    env_vars: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="Environment variables injected into the container",
    )
    ports: Optional[Dict[int, int]] = Field(
        default=None,
        description="Port mappings: {host_port: container_port}",
    )
    volumes: Optional[Dict[str, str]] = Field(
        default=None,
        description="Volume mappings: {host_dir: container_dir}"
    )
    depends_on: Optional[List[str]] = Field(
        default_factory=list,
        description="Names of containers this container depends on. Dependent containers will wait until these are RUNNING."
    )
    mem_limit: Optional[str] = Field(
        default="512m",
        description="Docker memory limit string, e.g. '512m', '1g'. Passed directly to the Docker daemon.",
    )
    cpu_limit: Optional[str] = Field(
        default=None,
        description="Docker CPU limit string, e.g. '0.5' or '1'. Passed to the Docker daemon.",
    )

    @model_validator(mode="after")
    def check_image_or_git_url(self) -> "DeploymentContainerCreate":
        """Ensure exactly one source (image XOR git_url) is provided."""
        has_image = bool(self.image)
        has_git = bool(self.git_url)
        if has_image == has_git:  # both set or neither set
            raise ValueError(
                "Provide exactly one source: either 'image' or 'git_url', not both (or neither)."
            )
        return self


class DeploymentContainerRead(BaseModel):
    id: uuid.UUID
    container_id: Optional[str]
    name: str
    image: str
    role: str
    host_port: Optional[int] = None
    status: ContainerStatus

    model_config = ConfigDict(from_attributes=True)


# --- Deployment Schemas ---

class DeploymentCreate(BaseModel):
    """
    Schema for creating a multi-container deployment environment.
    Expects an optional network name and a list of container specs.
    """
    network_name: Optional[str] = Field(
        default=None,
        description="Custom Docker network name. Auto-generated if not provided.",
    )
    containers: List[DeploymentContainerCreate] = Field(
        ...,
        description="List of containers to deploy in this environment (min 1)",
    )

    @field_validator("containers")
    @classmethod
    def check_containers_not_empty(cls, v: List[DeploymentContainerCreate]) -> List[DeploymentContainerCreate]:
        if not v:
            raise ValueError("A deployment must contain at least one container.")
        return v


class DeploymentRead(BaseModel):
    """
    Schema for reading a deployment including all its containers.
    """
    id: uuid.UUID
    user_id: uuid.UUID
    network_name: Optional[str]
    status: DeploymentStatus
    created_at: datetime
    containers: List[DeploymentContainerRead] = []

    model_config = ConfigDict(from_attributes=True)
