from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

class VolumeBase(BaseModel):
    name: str = Field(..., description="The unique name of the standalone volume in Docker")

class VolumeCreate(VolumeBase):
    pass

class VolumeRead(VolumeBase):
    id: UUID
    user_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True
