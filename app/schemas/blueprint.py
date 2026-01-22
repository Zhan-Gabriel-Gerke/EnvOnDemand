import uuid
from typing import Optional, Dict, Any

from pydantic import BaseModel, ConfigDict


# --- Blueprint Schemas ---

class BlueprintBase(BaseModel):
    """Base schema for a blueprint."""
    name: str
    image_tag: str
    default_port: int
    default_env_vars: Dict[str, Any] = {}
    cpu_limit: Optional[str] = None


class BlueprintCreate(BlueprintBase):
    """Schema for creating a blueprint (Command)."""
    pass


class BlueprintRead(BlueprintBase):
    """Schema for reading a blueprint (Query)."""
    id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)
