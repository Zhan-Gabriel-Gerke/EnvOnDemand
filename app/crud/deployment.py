import uuid
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.models.models import (
    Deployment,
    DeploymentContainer,
    DeploymentStatus,
    ContainerStatus,
    Blueprint,
)
from app.schemas.deployment import DeploymentCreate, DeploymentContainerCreate


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

async def get_deployment(db: AsyncSession, deployment_id: uuid.UUID) -> Optional[Deployment]:
    """Fetch a single Deployment by its primary key (eager-loads containers)."""
    result = await db.execute(
        select(Deployment)
        .options(selectinload(Deployment.containers))
        .filter(Deployment.id == deployment_id)
    )
    return result.scalars().first()


async def get_deployments(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> List[Deployment]:
    """Fetch a paginated list of all Deployments (eager-loads containers)."""
    result = await db.execute(
        select(Deployment)
        .options(selectinload(Deployment.containers))
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Create helpers
# ---------------------------------------------------------------------------

async def create_multi_container_deployment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    deployment_in: DeploymentCreate,
) -> Deployment:
    """
    Persist a new multi-container Deployment and its DeploymentContainer rows.

    - Generates a unique ``network_name`` if one is not provided (required by
      the UNIQUE constraint on the column).
    - All containers start in ``PENDING`` status.
    - The Deployment itself also starts in ``PENDING``.
    """
    network_name = deployment_in.network_name or f"net-{uuid.uuid4().hex[:8]}"

    db_deployment = Deployment(
        user_id=user_id,
        network_name=network_name,
        status=DeploymentStatus.PENDING,
    )
    db.add(db_deployment)
    # Flush to generate the primary key so we can reference it in containers.
    await db.flush()

    for container_spec in deployment_in.containers:
        # Store the image source (image tag or a placeholder for git-built ones).
        image_label = container_spec.image or f"git:{container_spec.git_url}"
        db_container = DeploymentContainer(
            deployment_id=db_deployment.id,
            name=container_spec.name,
            image=image_label,
            role=container_spec.role,
            status=ContainerStatus.PENDING,
        )
        db.add(db_container)

    await db.commit()
    return await get_deployment(db, db_deployment.id)


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------

async def update_deployment_status(
    db: AsyncSession,
    *,
    deployment_id: uuid.UUID,
    status: DeploymentStatus,
    container_id: Optional[str] = None,   # kept for legacy compat
    external_port: Optional[int] = None,  # kept for legacy compat
) -> Optional[Deployment]:
    """Update a Deployment's status (and optionally container_id / external_port)."""
    db_deployment = await get_deployment(db, deployment_id)
    if db_deployment:
        db_deployment.status = status
        # These attributes exist on the old single-container Deployment model;
        # they may not be present on the new multi-container one — set safely.
        if container_id and hasattr(db_deployment, "container_id"):
            db_deployment.container_id = container_id
        if external_port and hasattr(db_deployment, "external_port"):
            db_deployment.external_port = external_port
        await db.commit()
        await db.refresh(db_deployment)
    return db_deployment


async def update_container_status(
    db: AsyncSession,
    *,
    container_db_id: uuid.UUID,
    status: ContainerStatus,
    docker_container_id: Optional[str] = None,
    host_port: Optional[int] = None,
) -> Optional[DeploymentContainer]:
    """Update a DeploymentContainer's runtime status, Docker container ID, and host port."""
    result = await db.execute(
        select(DeploymentContainer).filter(DeploymentContainer.id == container_db_id)
    )
    db_container = result.scalars().first()
    if db_container:
        db_container.status = status
        if docker_container_id:
            db_container.container_id = docker_container_id
        if host_port is not None:
            db_container.host_port = host_port
        await db.commit()
        await db.refresh(db_container)
    return db_container


# ---------------------------------------------------------------------------
# Delete helpers
# ---------------------------------------------------------------------------

async def delete_deployment(
    db: AsyncSession, deployment_id: uuid.UUID
) -> Optional[Deployment]:
    """Delete a Deployment (cascade removes its DeploymentContainers)."""
    db_deployment = await get_deployment(db, deployment_id)
    if db_deployment:
        await db.delete(db_deployment)
        await db.commit()
    return db_deployment


# ---------------------------------------------------------------------------
# Blueprint helper (used by old single-container flow — kept for compat)
# ---------------------------------------------------------------------------

async def get_blueprint(db: AsyncSession, blueprint_id: uuid.UUID) -> Optional[Blueprint]:
    """Fetch a Blueprint by ID."""
    result = await db.execute(select(Blueprint).filter(Blueprint.id == blueprint_id))
    return result.scalars().first()


# Legacy alias expected by existing tests
async def create_deployment(
    db: AsyncSession,
    *,
    deployment_in,
    blueprint: Optional[Blueprint] = None,
) -> Deployment:
    """Thin legacy wrapper — kept so existing tests don't break.
    For new multi-container deployments use create_multi_container_deployment."""
    image_tag = (blueprint.image_tag if blueprint else None) or getattr(deployment_in, "image_tag", None)
    env_vars = (blueprint.default_env_vars if blueprint else {}) or {}
    if getattr(deployment_in, "env_vars", None):
        env_vars = {**env_vars, **deployment_in.env_vars}
    cpu_limit = (blueprint.cpu_limit if blueprint else None) or getattr(deployment_in, "cpu_limit", None)
    internal_port = (blueprint.default_port if blueprint else None) or getattr(deployment_in, "internal_port", None)

    if not image_tag:
        raise ValueError("Image tag must be provided either via blueprint or ad-hoc config.")
    if not internal_port:
        raise ValueError("Internal port must be provided either via blueprint or ad-hoc config.")

    db_deployment = Deployment(
        project_id=getattr(deployment_in, "project_id", None),
        blueprint_id=getattr(deployment_in, "blueprint_id", None),
        image_tag=image_tag,
        env_vars=env_vars,
        cpu_limit=cpu_limit,
        internal_port=internal_port,
        status=DeploymentStatus.PENDING,
    )
    db.add(db_deployment)
    await db.commit()
    await db.refresh(db_deployment)
    return db_deployment
