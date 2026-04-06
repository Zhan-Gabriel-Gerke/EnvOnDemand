from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import UUID4

from app.models.models import Volume
from app.schemas.volume import VolumeCreate

async def get_volume(db: AsyncSession, volume_id: UUID4) -> Optional[Volume]:
    """Retrieve a single volume by its UUID."""
    result = await db.execute(select(Volume).filter(Volume.id == volume_id))
    return result.scalars().first()

async def get_volume_by_name(db: AsyncSession, name: str) -> Optional[Volume]:
    """Retrieve a single volume by its unique name."""
    result = await db.execute(select(Volume).filter(Volume.name == name))
    return result.scalars().first()

async def get_user_volumes(db: AsyncSession, user_id: UUID4) -> List[Volume]:
    """Retrieve all volumes owned by a specific user."""
    result = await db.execute(select(Volume).filter(Volume.user_id == user_id).order_by(Volume.created_at.desc()))
    return list(result.scalars().all())

async def create_volume(db: AsyncSession, volume_in: VolumeCreate, user_id: UUID4) -> Volume:
    """Create a new volume record in the database."""
    db_volume = Volume(
        name=volume_in.name,
        user_id=user_id
    )
    db.add(db_volume)
    await db.commit()
    await db.refresh(db_volume)
    return db_volume

async def delete_volume(db: AsyncSession, volume_id: UUID4) -> None:
    """Delete a volume record from the database."""
    db_volume = await get_volume(db, volume_id)
    if db_volume:
        await db.delete(db_volume)
        await db.commit()
