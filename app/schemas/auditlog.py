import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.models import AuditedEntityType


# --- AuditLog Schemas ---

# Audit logs are read-only, so we only need a "Read" schema.
class AuditLogRead(BaseModel):
    """Schema for reading an audit log entry (Query)."""
    id: uuid.UUID
    action: str
    entity_type: AuditedEntityType
    entity_id: str
    timestamp: datetime
    user_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)
