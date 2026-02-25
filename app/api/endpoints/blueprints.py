import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import blueprint as crud_blueprint
from app.db.session import get_db
from app.schemas.blueprint import BlueprintCreate, BlueprintRead, BlueprintUpdate

router = APIRouter()


@router.get("/", response_model=List[BlueprintRead])
async def list_blueprints(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
):
    """
    Retrieve blueprints.
    """
    blueprints = await crud_blueprint.get_blueprints(db, skip=skip, limit=limit)
    return blueprints


@router.post("/", response_model=BlueprintRead, status_code=201)
async def create_blueprint(
    *,
    db: AsyncSession = Depends(get_db),
    blueprint_in: BlueprintCreate,
):
    """
    Create new blueprint.
    """
    blueprint = await crud_blueprint.create_blueprint(db=db, blueprint_in=blueprint_in)
    return blueprint


@router.get("/{blueprint_id}", response_model=BlueprintRead)
async def read_blueprint(
    *,
    db: AsyncSession = Depends(get_db),
    blueprint_id: uuid.UUID,
):
    """
    Get blueprint by ID.
    """
    project = await crud_blueprint.get_blueprint(db, blueprint_id=blueprint_id)
    if not project:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return project


@router.patch("/{blueprint_id}", response_model=BlueprintRead)
async def update_blueprint(
    *,
    db: AsyncSession = Depends(get_db),
    blueprint_id: uuid.UUID,
    blueprint_in: BlueprintUpdate,
):
    """
    Update blueprint by ID.
    """
    blueprint = await crud_blueprint.get_blueprint(db, blueprint_id=blueprint_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    
    blueprint = await crud_blueprint.update_blueprint(db=db, db_obj=blueprint, obj_in=blueprint_in)
    return blueprint
