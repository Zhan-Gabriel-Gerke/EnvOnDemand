from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.models import User
from app.schemas.volume import VolumeCreate, VolumeRead
from app.crud.volume import get_user_volumes, get_volume_by_name, create_volume, delete_volume, get_volume
from app.services.docker_service import DockerService, DockerServiceError

router = APIRouter()

@router.get("/volumes", response_model=List[VolumeRead])
async def read_volumes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve all standalone volumes for the current user."""
    return await get_user_volumes(db, user_id=current_user.id)

@router.post("/volumes", response_model=VolumeRead, status_code=status.HTTP_201_CREATED)
async def create_new_volume(
    volume_in: VolumeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new Docker standalone volume."""
    existing_vol = await get_volume_by_name(db, name=volume_in.name)
    if existing_vol:
        raise HTTPException(
            status_code=400,
            detail=f"A volume named '{volume_in.name}' already exists."
        )

    try:
        async with DockerService() as docker_service:
            await docker_service.create_volume(volume_in.name)
    except DockerServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return await create_volume(db, volume_in, current_user.id)

@router.delete("/volumes/{volume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_volume(
    volume_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a Docker standalone volume."""
    volume = await get_volume(db, volume_id)
    if not volume:
        raise HTTPException(status_code=404, detail="Volume not found.")
    
    if volume.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this volume.")

    try:
        async with DockerService() as docker_service:
            await docker_service.remove_volume(volume.name)
    except DockerServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await delete_volume(db, volume_id)
    return None
