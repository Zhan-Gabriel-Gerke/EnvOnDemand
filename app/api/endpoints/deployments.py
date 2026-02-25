import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.deployment import DeploymentRead, DeploymentCreate
from app.crud import deployment as crud
from app.db.session import get_db, AsyncSessionLocal
from app.models.models import DeploymentStatus
from app.services.docker_service import DockerService, DockerServiceError

router = APIRouter()


async def run_deployment_task_async(deployment_id: uuid.UUID):
    """
    Background task to run a container and update the deployment status.
    Uses its own DB session and DockerService context.
    """
    async with AsyncSessionLocal() as db:
        async with DockerService() as docker_service:
            try:
                deployment = await crud.get_deployment(db, deployment_id)
                if not deployment:
                    print(f"Deployment {deployment_id} not found for background task.")
                    return

                print(f"Running container for deployment {deployment.id}...")
                run_info = await docker_service.run_container(
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


def run_deployment_task(deployment_id: uuid.UUID):
    """
    Wrapper to run the async task in the background.
    """
    import asyncio
    asyncio.run(run_deployment_task_async(deployment_id))


@router.post("/deployments", response_model=DeploymentRead, status_code=202)
async def create_deployment(
    deployment_in: DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new deployment.
    This endpoint immediately returns a 'pending' deployment and starts
    the container creation process in the background.
    """
    blueprint = None
    if deployment_in.blueprint_id:
        blueprint = await crud.get_blueprint(db, blueprint_id=deployment_in.blueprint_id)
        if not blueprint:
            raise HTTPException(status_code=404, detail="Blueprint not found.")

    # 1. Create the DB record with 'pending' status
    try:
        db_deployment = await crud.create_deployment(
            db=db, deployment_in=deployment_in, blueprint=blueprint
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Add the container creation to background tasks
    background_tasks.add_task(run_deployment_task_async, db_deployment.id)

    # 3. Return the accepted deployment object immediately
    return db_deployment


@router.get("/deployments", response_model=List[DeploymentRead])
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
        async with DockerService() as docker_service:
            try:
                await docker_service.stop_container(db_deployment.container_id)
            except DockerServiceError as e:
                # Log the error but proceed with DB deletion
                print(f"Could not remove container {db_deployment.container_id}: {e}")
            except ConnectionError as e:
                print(f"Could not connect to Docker to remove container: {e}")
            except Exception as e:
                 print(f"Unexpected error removing container: {e}")


    # Delete the database record
    await crud.delete_deployment(db, deployment_id=deployment_id)

    return


@router.get("/deployments/{deployment_id}/logs")
async def get_deployment_logs(
    deployment_id: uuid.UUID,
    tail: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """
    Get logs for a running or stopped deployment.
    """
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")
    
    if not db_deployment.container_id:
        # If no container ID, maybe it failed before starting or is pending
        return {"logs": "Container not started yet or no container ID available."}

    async with DockerService() as docker_service:
        try:
            logs = await docker_service.get_container_logs(db_deployment.container_id, tail=tail)
            return {"logs": logs}
        except DockerServiceError as e:
            # It's possible the container is gone but we have an ID.
            return {"logs": f"Could not retrieve logs: {e}"}
        except Exception as e:
             raise HTTPException(status_code=500, detail=str(e))


@router.post("/deployments/{deployment_id}/stop")
async def stop_deployment(
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Stop a deployment (remove container, keep DB record).
    """
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    if db_deployment.status == DeploymentStatus.STOPPED:
        return {"message": "Already stopped"}

    if db_deployment.container_id:
        async with DockerService() as docker_service:
            try:
                await docker_service.stop_container(db_deployment.container_id)
            except DockerServiceError as e:
                print(f"Error stopping container: {e}")
                # We still update status to STOPPED if we intended to stop it?
                # Maybe it's already gone.
                pass
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
    
    await crud.update_deployment_status(
        db, deployment_id=deployment_id, status=DeploymentStatus.STOPPED, container_id=None, external_port=None
    )
    return {"message": "Deployment stopped"}


@router.post("/deployments/{deployment_id}/start")
async def start_deployment(
    deployment_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a stopped deployment.
    """
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    if db_deployment.status == DeploymentStatus.RUNNING:
        return {"message": "Already running"}

    # Set to PENDING first
    await crud.update_deployment_status(
        db, deployment_id=deployment_id, status=DeploymentStatus.PENDING
    )

    # Re-trigger the background task with the existing ID
    background_tasks.add_task(run_deployment_task_async, db_deployment.id)
    
    return {"message": "Deployment starting"}
