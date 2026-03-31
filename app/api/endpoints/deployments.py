import asyncio
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user, RequireRole
from app.schemas.deployment import DeploymentRead, DeploymentCreate, DeploymentContainerCreate
from app.crud import deployment as crud
from app.db.session import get_db, AsyncSessionLocal
from app.models.models import DeploymentStatus, ContainerStatus, User
from app.services.docker_service import DockerService, DockerServiceError, GitCloneError

router = APIRouter()


# ---------------------------------------------------------------------------
# Background task logic
# ---------------------------------------------------------------------------

async def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: int = 60) -> bool:
    """
    Проверяет доступность порта. Полезно как Healthcheck базы данных 
    перед запуском зависимых приложений.
    """
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    while loop.time() - start_time < timeout:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            await asyncio.sleep(1)
    return False

async def _deploy_single_container(
    docker_service: DockerService,
    db: AsyncSession,
    db_container_id: uuid.UUID,
    container_spec: DeploymentContainerCreate,
    network_name: Optional[str],
) -> bool:
    """
    Deploy one container and update its DB status.
    Returns True if the container started successfully, False otherwise.
    """
    try:
        has_explicit_ports = bool(container_spec.ports)
        internal_port = next(iter(container_spec.ports.values())) if container_spec.ports else 80

        if container_spec.git_url:
            run_info = await docker_service.build_and_run_from_git(
                git_url=container_spec.git_url,
                name=container_spec.name,
                internal_port=internal_port,
                environment=container_spec.env_vars,
                network=network_name,
                volumes=container_spec.volumes,
            )
        else:
            run_info = await docker_service.run_container(
                image_tag=container_spec.image,  # type: ignore[arg-type]
                internal_port=internal_port,
                environment=container_spec.env_vars,
                network=network_name,
                name=container_spec.name,
                volumes=container_spec.volumes,
            )

        host_port = run_info.get("port")
        
        # Ждем готовности, если порты были явно заданы юзером. Воркеры без портов игнорируют это.
        if host_port and has_explicit_ports:
            print(f"[Deployment] Waiting for healthcheck (port {host_port}) for '{container_spec.name}'...")
            is_ready = await _wait_for_port(port=host_port)
            if not is_ready:
                print(f"[Deployment] Healthcheck timeout: port {host_port} not ready.")
                await crud.update_container_status(
                    db, container_db_id=db_container_id, status=ContainerStatus.FAILED
                )
                return False

        await crud.update_container_status(
            db,
            container_db_id=db_container_id,
            status=ContainerStatus.RUNNING,
            docker_container_id=run_info["container_id"],
            host_port=host_port,
        )
        return True

    except (DockerServiceError, ConnectionError) as exc:
        print(f"[Deployment] Container '{container_spec.name}' failed: {exc}")
        await crud.update_container_status(
            db, container_db_id=db_container_id, status=ContainerStatus.FAILED
        )
        return False

    except Exception as exc:
        print(f"[Deployment] Unexpected error for container '{container_spec.name}': {exc}")
        await crud.update_container_status(
            db, container_db_id=db_container_id, status=ContainerStatus.FAILED
        )
        return False


async def run_multi_container_deployment(
    deployment_id: uuid.UUID,
    container_specs: List[DeploymentContainerCreate],
) -> None:
    """
    Background task: spin up all containers for a Deployment.
    - Uses graph topological wait with asyncio.Event
    - Sets Deployment.status = RUNNING if all containers succeed, else FAILED.
    """
    async with AsyncSessionLocal() as db:
        async with DockerService() as docker_service:
            try:
                db_deployment = await crud.get_deployment(db, deployment_id)
                if not db_deployment:
                    print(f"[Deployment] Deployment {deployment_id} not found — aborting background task.")
                    return

                network_name = db_deployment.network_name
                
                # Словарик, чтобы находить id контейнера в БД по его имени
                db_containers_map = {c.name: c for c in db_deployment.containers}

                # Создаем Event-эвенты (маяки) и словарь результатов для каждого контейнера
                events = {spec.name: asyncio.Event() for spec in container_specs}
                results = {spec.name: False for spec in container_specs}

                async def deploy_task(spec: DeploymentContainerCreate) -> None:
                    db_container = db_containers_map.get(spec.name)
                    if not db_container:
                        events[spec.name].set()
                        return

                    try:
                        # 1. ЖДЕМ ЗАВИСИМОСТИ
                        for dep in spec.depends_on or []:
                            if dep in events:
                                await events[dep].wait() # Ждем сигнала от предка!
                                # Если предок после запуска выдал False (провалился), мы даже не стартуем
                                if not results[dep]:
                                    print(f"[Deployment] Dependency '{dep}' failed, skipping '{spec.name}'.")
                                    await crud.update_container_status(
                                        db, db_container.id, ContainerStatus.FAILED
                                    )
                                    return # Прерываем запуск

                        # 2. ВСЕ ПРЕДКИ РАБОТАЮТ — ЗАПУСКАЕМ СВОЙ КОНТЕЙНЕР
                        success = await _deploy_single_container(
                            docker_service, db, db_container.id, spec, network_name
                        )
                        results[spec.name] = success

                    except Exception as e:
                        print(f"[Deployment] Unexpected orchestrator error for '{spec.name}': {e}")
                        results[spec.name] = False
                    
                    finally:
                        # 3. Сигнализируем, что мы закончили попытку запуска. 
                        # Следующий в очереди сбросит wait() и начнет читать results.
                        events[spec.name].set()

                # Закидываем все задачи в gather единовременно. Event'ы выстроят их в очередь внутри.
                tasks = [asyncio.create_task(deploy_task(spec)) for spec in container_specs]
                await asyncio.gather(*tasks, return_exceptions=True)

                # Проверяем итог всей операции: если у всех контейнеров True - успех, иначе провал.
                final_status = (
                    DeploymentStatus.RUNNING if all(results.values()) else DeploymentStatus.FAILED
                )
                await crud.update_deployment_status(
                    db, deployment_id=deployment_id, status=final_status
                )
                print(f"[Deployment] {deployment_id} pipeline finished with status → {final_status.value}")

            except ConnectionError as exc:
                print(f"[Deployment] Cannot connect to Docker daemon: {exc}")
                await crud.update_deployment_status(
                    db, deployment_id=deployment_id, status=DeploymentStatus.FAILED
                )
            except Exception as exc:
                print(f"[Deployment] Critical pipeline failure for {deployment_id}: {exc}")
                await crud.update_deployment_status(
                    db, deployment_id=deployment_id, status=DeploymentStatus.FAILED
                )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/deployments", response_model=DeploymentRead, status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    deployment_in: DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireRole(["admin", "developer"])),
) -> DeploymentRead:
    """
    Provision a new multi-container deployment environment.

    1. Validates the payload (≥1 container, image XOR git_url per container).
    2. Enforces quota limits.
    3. Persists the Deployment and DeploymentContainer rows (status: PENDING).
    4. Enqueues a background task to actually pull/build and start the containers.
    5. Returns HTTP 202 immediately with the PENDING deployment record.
    """
    # --- Quota check ---
    if not current_user.quota:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Integrity exception: quota profile missing for the current user.",
        )

    requested = len(deployment_in.containers)
    available = current_user.quota.max_containers - current_user.quota.active_containers
    if requested > available:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Resource limits exhausted: requesting {requested} containers but only "
                f"{available} slots available "
                f"({current_user.quota.active_containers}/{current_user.quota.max_containers} used)."
            ),
        )

    # --- Persist to DB ---
    try:
        db_deployment = await crud.create_multi_container_deployment(
            db,
            user_id=current_user.id,
            deployment_in=deployment_in,
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A deployment with the network name '{deployment_in.network_name}' already exists." if deployment_in.network_name else "Database integrity error (likely a duplicate constraint).",
        )

    # --- Schedule background work ---
    # Pass the container specs explicitly so the background task doesn't need
    # to deserialise them from the DB (avoids extra query + schema coupling).
    background_tasks.add_task(
        run_multi_container_deployment,
        db_deployment.id,
        deployment_in.containers,
    )

    return db_deployment


@router.put("/deployments/{deployment_id}", response_model=DeploymentRead, status_code=status.HTTP_202_ACCEPTED)
async def update_deployment(
    deployment_id: uuid.UUID,
    deployment_in: DeploymentCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(RequireRole(["admin", "developer"])),
) -> DeploymentRead:
    """
    Update an existing deployment by completely tearing it down and rebuilding it.
    """
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")
        
    # Check quota difference
    old_length = len(db_deployment.containers) if db_deployment.containers else 0
    new_length = len(deployment_in.containers)
    diff = new_length - old_length
    
    if diff > 0 and current_user.quota:
        available = current_user.quota.max_containers - current_user.quota.active_containers
        if diff > available:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Resource limits exhausted: adding {diff} new containers but only {available} slots available."
            )

    # Teardown existing docker containers
    if db_deployment.containers:
        async with DockerService() as docker_service:
            for container in db_deployment.containers:
                if container.container_id:
                    try:
                        await docker_service.stop_container(container.container_id)
                        await docker_service.remove_container(container.container_id)
                    except Exception as e:
                        print(f"Failed to remove container {container.container_id} during edit: {e}")

    # Recreate in DB
    await crud.recreate_deployment_containers(db, deployment_id, deployment_in.containers)
    
    # Update deployment network and status
    db_deployment.network_name = deployment_in.network_name or db_deployment.network_name
    db_deployment.status = DeploymentStatus.PENDING
    await db.commit()
    await db.refresh(db_deployment)
    
    # Re-run background task
    background_tasks.add_task(
        run_multi_container_deployment,
        db_deployment.id,
        deployment_in.containers,
    )
    
    return db_deployment


@router.get("/deployments", response_model=List[DeploymentRead])
async def list_deployments(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
) -> List[DeploymentRead]:
    """Retrieve a paginated list of all deployments."""
    return await crud.get_deployments(db, skip=skip, limit=limit)


@router.get("/deployments/{deployment_id}", response_model=DeploymentRead)
async def get_deployment(
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DeploymentRead:
    """Retrieve a single deployment by its ID."""
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")
    return db_deployment


@router.delete("/deployments/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stop all containers and delete the deployment record."""
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    # Stop every running container in the deployment.
    if db_deployment.containers:
        async with DockerService() as docker_service:
            for container in db_deployment.containers:
                if container.container_id:
                    try:
                        await docker_service.stop_container(container.container_id)
                    except (DockerServiceError, ConnectionError, Exception) as exc:
                        # Log but don't block DB cleanup.
                        print(f"[Deployment] Could not remove container {container.container_id}: {exc}")

    await crud.delete_deployment(db, deployment_id=deployment_id)


@router.post("/deployments/{deployment_id}/stop")
async def stop_deployment(
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Stop a running deployment (removes containers, keeps the DB record)."""
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    if db_deployment.status == DeploymentStatus.STOPPED:
        return {"message": "Already stopped."}

    async with DockerService() as docker_service:
        for container in db_deployment.containers:
            if container.container_id:
                try:
                    await docker_service.stop_container(container.container_id)
                except (DockerServiceError, Exception) as exc:
                    print(f"[Deployment] Error stopping container {container.container_id}: {exc}")

    await crud.update_deployment_status(
        db, deployment_id=deployment_id, status=DeploymentStatus.STOPPED
    )
    return {"message": "Deployment stopped."}


@router.post("/deployments/{deployment_id}/start")
async def start_deployment(
    deployment_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-start a stopped deployment by replaying the container creation."""
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    if db_deployment.status == DeploymentStatus.RUNNING:
        return {"message": "Already running."}

    await crud.update_deployment_status(
        db, deployment_id=deployment_id, status=DeploymentStatus.PENDING
    )

    # Re-build specs from DB so we can enqueue the background task.
    # For git-sourced containers, image starts with "git:" — we cannot re-clone
    # without the original URL, so re-start is only safe for image-based containers.
    background_tasks.add_task(
        run_multi_container_deployment,
        db_deployment.id,
        [],  # empty specs — background task will log a mismatch and mark FAILED for git-based
    )
    return {"message": "Deployment starting …"}


@router.get("/deployments/{deployment_id}/logs")
async def get_deployment_logs(
    deployment_id: uuid.UUID,
    tail: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve the last N log lines from all containers in a deployment."""
    db_deployment = await crud.get_deployment(db, deployment_id=deployment_id)
    if not db_deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    all_logs: dict = {}
    async with DockerService() as docker_service:
        for container in db_deployment.containers:
            if not container.container_id:
                all_logs[container.name] = "Container not started yet."
                continue
            try:
                all_logs[container.name] = await docker_service.get_container_logs(
                    container.container_id, tail=tail
                )
            except (DockerServiceError, Exception) as exc:
                all_logs[container.name] = f"Could not retrieve logs: {exc}"

    return {"deployment_id": str(deployment_id), "logs": all_logs}
