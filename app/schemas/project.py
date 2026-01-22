import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict
from .deployment import DeploymentRead


# --- Project Schemas ---

class ProjectBase(BaseModel):
    """Base schema for a project."""
    name: str
    description: Optional[str] = None


class ProjectCreate(ProjectBase):
    """Schema for creating a project (Command)."""
    pass


class ProjectRead(ProjectBase):
    """Schema for reading a project, including its owner and deployments (Query)."""
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime
    deployments: List[DeploymentRead] = []

    model_config = ConfigDict(from_attributes=True)
