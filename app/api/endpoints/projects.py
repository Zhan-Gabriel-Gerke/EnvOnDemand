import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import project as crud_project
from app.crud import user as crud_user
from app.db.session import get_db
from app.schemas.project import ProjectCreate, ProjectRead
from app.schemas.user import UserCreate

router = APIRouter()


@router.get("/", response_model=List[ProjectRead])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
):
    """
    Retrieve projects.
    """
    projects = await crud_project.get_projects(db, skip=skip, limit=limit)
    return projects


@router.post("/", response_model=ProjectRead, status_code=201)
async def create_project(
    *,
    db: AsyncSession = Depends(get_db),
    project_in: ProjectCreate,
):
    """
    Create new project.
    """
    # Verify owner exists or default to admin
    owner_id = project_in.owner_id
    if not owner_id:
        # Default to admin user for dev/demo purposes
        admin_user = await crud_user.get_user_by_username(db, username="admin")
        if not admin_user:
            # Create admin user if not exists
            admin_user = await crud_user.create_user(db=db, user_in=UserCreate(
                username="admin",
                email="admin@example.com",
                password="admin", # Plaintext for demo
                is_admin=True
            ))
        owner_id = admin_user.id
    
    try:
        project = await crud_project.create_project(db=db, project_in=project_in, owner_id=owner_id)
    except Exception as e:
        await db.rollback()
        if "uq_owner_project_name" in str(e) or "UniqueViolation" in str(type(e).__name__):
            raise HTTPException(
                status_code=409,
                detail=f"Проект с именем '{project_in.name}' уже существует."
            )
        raise HTTPException(status_code=500, detail=str(e))
    return project


@router.get("/{project_id}", response_model=ProjectRead)
async def read_project(
    *,
    db: AsyncSession = Depends(get_db),
    project_id: uuid.UUID,
):
    """
    Get project by ID.
    """
    project = await crud_project.get_project(db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    *,
    db: AsyncSession = Depends(get_db),
    project_id: uuid.UUID,
):
    """
    Delete a project and all its deployments.
    """
    project = await crud_project.get_project(db, project_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await crud_project.delete_project(db, project_id=project_id)
    return
