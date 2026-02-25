from typing import List, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.models import Blueprint
from app.schemas.blueprint import BlueprintCreate, BlueprintUpdate


async def get_blueprint(db: AsyncSession, blueprint_id: uuid.UUID) -> Optional[Blueprint]:
    """Get a blueprint by ID."""
    result = await db.execute(select(Blueprint).filter(Blueprint.id == blueprint_id))
    return result.scalars().first()


async def get_blueprints(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Blueprint]:
    """Get a list of all blueprints."""
    result = await db.execute(select(Blueprint).offset(skip).limit(limit))
    return result.scalars().all()


async def create_blueprint(db: AsyncSession, *, blueprint_in: BlueprintCreate) -> Blueprint:
    """Create a new blueprint."""
    db_blueprint = Blueprint(
        name=blueprint_in.name,
        image_tag=blueprint_in.image_tag,
        default_port=blueprint_in.default_port,
        default_env_vars=blueprint_in.default_env_vars,
        cpu_limit=blueprint_in.cpu_limit
    )
    db.add(db_blueprint)
    await db.commit()
    await db.refresh(db_blueprint)
    return db_blueprint


async def update_blueprint(
    db: AsyncSession, *, db_obj: Blueprint, obj_in: BlueprintUpdate
) -> Blueprint:
    """Update a blueprint."""
    update_data = obj_in.model_dump(exclude_unset=True)
    for field in update_data:
        setattr(db_obj, field, update_data[field])
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj
