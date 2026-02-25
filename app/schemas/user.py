import uuid
from pydantic import BaseModel, ConfigDict, EmailStr


# --- User Schemas ---

class UserBase(BaseModel):
    """Base schema with common user fields."""
    username: str
    email: EmailStr


class UserCreate(UserBase):
    """Schema for creating a user (Command). Requires a password."""
    password: str
    is_admin: bool = False


class UserRead(UserBase):
    """Schema for reading a user (Query). Excludes sensitive info like password."""
    id: uuid.UUID
    is_admin: bool

    model_config = ConfigDict(from_attributes=True)
