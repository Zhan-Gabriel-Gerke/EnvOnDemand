import uuid
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.models import Deployment, Blueprint, DeploymentStatus
from app.schemas.deployment import DeploymentCreate


async def get_deployment(db: AsyncSession, deployment_id: uuid.UUID) -> Optional[Deployment]:
    """Get a single deployment by its ID."""
    result = await db.execute(select(Deployment).filter(Deployment.id == deployment_id))
    return result.scalars().first()


async def get_deployments(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Deployment]:
    """Get a list of all deployments."""
    result = await db.execute(select(Deployment).offset(skip).limit(limit))
    return result.scalars().all()


async def create_deployment_from_blueprint(db: AsyncSession, *, deployment_in: DeploymentCreate, blueprint: Blueprint) -> Deployment:
    """Create a new deployment record in the database from a blueprint."""
    # Snapshotting configuration from blueprint to deployment
    db_deployment = Deployment(
        project_id=deployment_in.project_id,
        blueprint_id=deployment_in.blueprint_id,
        image_tag=blueprint.image_tag,
        env_vars=blueprint.default_env_vars,
        cpu_limit=blueprint.cpu_limit,
        internal_port=blueprint.default_port,
        status=DeploymentStatus.PENDING  # Start with PENDING status
    )
    db.add(db_deployment)
    await db.commit()
    await db.refresh(db_deployment)
    return db_deployment


async def update_deployment_status(
    db: AsyncSession,
    *,
    deployment_id: uuid.UUID,
    status: DeploymentStatus,
    container_id: Optional[str] = None,
    external_port: Optional[int] = None
) -> Optional[Deployment]:
    """Update a deployment's status, container_id, and port."""
    db_deployment = await get_deployment(db, deployment_id)
    if db_deployment:
        db_deployment.status = status
        if container_id:
            db_deployment.container_id = container_id
        if external_port:
            db_deployment.external_port = external_port
        await db.commit()
        await db.refresh(db_deployment)
    return db_deployment


async def delete_deployment(db: AsyncSession, deployment_id: uuid.UUID) -> Optional[Deployment]:
    """Delete a deployment from the database."""
    db_deployment = await get_deployment(db, deployment_id)
    if db_deployment:
        await db.delete(db_deployment)
        await db.commit()
    return db_deployment

# Also need a CRUD function for Blueprint
async def get_blueprint(db: AsyncSession, blueprint_id: uuid.UUID) -> Optional[Blueprint]:
    """Get a blueprint by ID."""
    result = await db.execute(select(Blueprint).filter(Blueprint.id == blueprint_id))
    return result.scalars().first()
