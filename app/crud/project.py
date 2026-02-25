from typing import List, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.models import Project
from app.schemas.project import ProjectCreate


async def get_project(db: AsyncSession, project_id: uuid.UUID) -> Optional[Project]:
    """Get a project by ID."""
    result = await db.execute(select(Project).filter(Project.id == project_id))
    return result.scalars().first()


async def get_projects(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Project]:
    """Get a list of all projects."""
    result = await db.execute(select(Project).offset(skip).limit(limit))
    return result.scalars().all()


async def create_project(db: AsyncSession, *, project_in: ProjectCreate, owner_id: uuid.UUID) -> Project:
    """Create a new project."""
    db_project = Project(
        name=project_in.name,
        description=project_in.description,
        owner_id=owner_id
    )
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project


async def delete_project(db: AsyncSession, project_id: uuid.UUID) -> None:
    """Delete a project (and cascade-delete its deployments)."""
    project = await get_project(db, project_id)
    if project:
        await db.delete(project)
        await db.commit()
