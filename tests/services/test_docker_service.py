import pytest
from unittest.mock import patch, MagicMock

# from sqlalchemy.ext.asyncio import AsyncSession # Not needed for unit tests without DB

from app.services.docker_service import DockerService, DockerImageError
from app.crud.deployment import create_deployment_from_blueprint, update_deployment_status, get_deployment
from app.models.models import Blueprint, Project, User, DeploymentStatus
from app.schemas.deployment import DeploymentCreate

DOCKER_MODULE_PATH = "app.services.docker_service.docker"

@pytest.mark.asyncio
@patch(DOCKER_MODULE_PATH)
async def test_run_container_success(mock_docker_module):
    """
    Tests the successful scenario of running a container (Async).
    """
    # --- 1. Arrange ---
    mock_docker_client = MagicMock()
    mock_docker_module.from_env.return_value = mock_docker_client

    mock_container = MagicMock()
    mock_container.id = "test_container_id_123"
    mock_docker_client.containers.run.return_value = mock_container

    # --- 2. Act ---
    async with DockerService() as docker_service:
        # Patch the sync method directly on the instance, or the class
        with patch.object(docker_service, '_find_free_port_sync', return_value=54321) as mock_find_port:
            result = await docker_service.run_container(
                image_tag="test-image:latest",
                internal_port=8080,
                environment={"VAR": "value"},
                cpu_limit="1.5"
            )

    # --- 3. Assert ---
    mock_find_port.assert_called_once()
    mock_docker_client.images.pull.assert_called_once_with("test-image:latest")
    mock_docker_client.containers.run.assert_called_once_with(
        image="test-image:latest",
        detach=True,
        ports={'8080/tcp': 54321},
        environment={"VAR": "value"},
        nano_cpus=1_500_000_000
    )
    assert result == {
        "container_id": "test_container_id_123",
        "port": 54321
    }

@pytest.mark.asyncio
@patch(DOCKER_MODULE_PATH)
async def test_run_container_image_not_found(mock_docker_module):
    """
    Tests the scenario where the Docker image is not found.
    """
    # --- Arrange ---
    mock_docker_client = MagicMock()
    mock_docker_module.from_env.return_value = mock_docker_client

    from docker.errors import ImageNotFound
    mock_docker_client.images.pull.side_effect = ImageNotFound("Image not found")

    # --- Act & Assert ---
    async with DockerService() as docker_service:
        with pytest.raises(DockerImageError, match="Image 'test-image:latest' not found."):
            await docker_service.run_container(
                image_tag="test-image:latest",
                internal_port=8080
            )

@pytest.mark.skip(reason="Requires running DB")
@pytest.mark.asyncio
@patch(DOCKER_MODULE_PATH)
async def test_run_container_and_save_to_db(mock_docker_module, db_session):
    """
    Hybrid test:
    - MOCK: DockerService and its calls to the Docker API.
    - REAL: Writing and reading from a real database.
    """
    # --- 1. Arrange ---
    mock_docker_client = MagicMock()
    mock_docker_module.from_env.return_value = mock_docker_client
    
    mock_container = MagicMock()
    mock_container.id = "fake_container_id_for_test_123"
    mock_docker_client.containers.run.return_value = mock_container

    test_user = User(username="testuser", email="test@test.com", hashed_password="...")
    db_session.add(test_user)
    await db_session.flush()

    test_project = Project(name="Test Project", owner_id=test_user.id)
    db_session.add(test_project)
    await db_session.flush()

    test_blueprint = Blueprint(
        name="Test Blueprint",
        image_tag="nginx:latest",
        default_port=80,
        default_env_vars={"NGINX_HOST": "test.com"},
        cpu_limit="0.5"
    )
    db_session.add(test_blueprint)
    await db_session.commit()

    deployment_input = DeploymentCreate(project_id=test_project.id, blueprint_id=test_blueprint.id)
    db_deployment = await create_deployment_from_blueprint(
        db=db_session, deployment_in=deployment_input, blueprint=test_blueprint
    )
    assert db_deployment.status == DeploymentStatus.PENDING

    # --- 2. Act ---
    async with DockerService() as docker_service:
        with patch.object(docker_service, '_find_free_port_sync', return_value=8000):
            run_info = await docker_service.run_container(
                image_tag=db_deployment.image_tag,
                internal_port=db_deployment.internal_port,
                environment=db_deployment.env_vars,
                cpu_limit=db_deployment.cpu_limit
            )

    await update_deployment_status(
        db=db_session,
        deployment_id=db_deployment.id,
        status=DeploymentStatus.RUNNING,
        container_id=run_info["container_id"],
        external_port=run_info.get("port")
    )

    # --- 3. Assert ---
    mock_docker_client.containers.run.assert_called_once_with(
        image="nginx:latest",
        detach=True,
        ports={f"{test_blueprint.default_port}/tcp": 8000},
        environment={"NGINX_HOST": "test.com"},
        nano_cpus=500_000_000
    )

    await db_session.commit()
    
    from sqlalchemy import text
    result = await db_session.execute(
        text(f"SELECT container_id, status FROM deployments WHERE id = '{db_deployment.id}'")
    )
    saved_data = result.fetchone()

    assert saved_data is not None
    assert saved_data.container_id == "fake_container_id_for_test_123"
    assert saved_data.status == DeploymentStatus.RUNNING.value
