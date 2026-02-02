import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.crud import deployment as crud
from app.db.session import get_db
from app.models.models import DeploymentStatus
from app.services.docker_service import DockerService, DockerServiceError

router = APIRouter()


def run_deployment_task(deployment_id: uuid.UUID, db_provider: callable):
    """
    Background task to run a container and update the deployment status.
    """
    async def task():
        db: AsyncSession = await anext(db_provider())
        try:
            docker_service = DockerService()
            deployment = await crud.get_deployment(db, deployment_id)
            if not deployment:
                print(f"Deployment {deployment_id} not found for background task.")
                return

            print(f"Running container for deployment {deployment.id}...")
            run_info = docker_service.run_container(
                image_tag=deployment.image_tag,
                internal_port=deployment.internal_port,
                environment=deployment.env_vars,
                cpu_limit=deployment.cpu_limit,
            )

            await crud.update_deployment_status(
                db,
                deployment_id=deployment.id,
                status=DeploymentStatus.RUNNING,
                container_id=run_info["container_id"],
                external_port=run_info["port"],
            )
            print(f"Deployment {deployment.id} is now RUNNING.")

        except DockerServiceError as e:
            print(f"Failed to start container for deployment {deployment_id}: {e}")
            await crud.update_deployment_status(
                db, deployment_id=deployment_id, status=DeploymentStatus.FAILED
            )
        except Exception as e:
            print(f"An unexpected error occurred for deployment {deployment_id}: {e}")
            await crud.update_deployment_status(
                db, deployment_id=deployment_id, status=DeploymentStatus.FAILED
            )
        finally:
            await db.close()
    
    import asyncio
    asyncio.run(task())


@router.post("/deployments", response_model=schemas.DeploymentRead, status_code=202)
async def create_deployment(
    deployment_in: schemas.DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new deployment.
    This endpoint immediately returns a 'pending' deployment and starts
    the container creation process in the background.
    """
    blueprint = await crud.get_blueprint(db, blueprint_id=deployment_in.blueprint_id)
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found.")

    # 1. Create the DB record with 'pending' status
    db_deployment = await crud.create_deployment_from_blueprint(
        db=db, deployment_in=deployment_in, blueprint=blueprint
    )

    # 2. Add the container creation to background tasks
    background_tasks.add_task(run_deployment_task, db_deployment.id, get_db)

    # 3. Return the accepted deployment object immediately
    return db_deployment


@router.get("/deployments", response_model=List[schemas.DeploymentRead])
async def list_deployments(
    db: AsyncSession = Depends(get_db), skip: int = 0, limit: int = 100
):
    """
    Retrieve a list of all deployments.
    """
    deployments = await crud.get_deployments(db, skip=skip, limit=limit)
    return deployments


@router.delete("/deployments/{deployment_id}", status_code=204)
async def delete_deployment(
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Stop the container and delete the deployment record.
    """
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    # Stop container if it exists
    if db_deployment.container_id:
        try:
            docker_service = DockerService()
            docker_service.stop_container(db_deployment.container_id)
        except DockerServiceError as e:
            # Log the error but proceed with DB deletion
            print(f"Could not remove container {db_deployment.container_id}: {e}")
        except ConnectionError as e:
            print(f"Could not connect to Docker to remove container: {e}")


    # Delete the database record
    await crud.delete_deployment(db, deployment_id=deployment_id)

    return
