"""
tests/crud/test_deployment.py
==============================
Tests for app.crud.deployment — multi-container deployment creation,
network name auto-generation, PENDING status initialisation, and
CASCADE deletes.

All tests hit the real PostgreSQL test database via the `db_session` fixture.
No Docker client is involved here — only the data-persistence layer.
"""
import pytest
import pytest_asyncio
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.crud.deployment import (
    create_multi_container_deployment,
    delete_deployment,
    get_deployment,
    get_deployments,
    update_container_status,
    update_deployment_status,
)
from app.crud.user import create_user
from app.models.models import (
    ContainerStatus,
    Deployment,
    DeploymentContainer,
    DeploymentStatus,
)
from app.schemas.deployment import DeploymentContainerCreate, DeploymentCreate
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """Seed a standard developer user for deployment tests."""
    user_in = UserCreate(
        username="deployuser",
        email="deploy@test.com",
        password="deploy_pw",
    )
    return await create_user(db_session, user_in=user_in)


@pytest_asyncio.fixture
async def single_container_deployment(db_session: AsyncSession, test_user):
    """
    Returns a pre-populated Deployment with one container.
    Useful as an Arrange shortcut in tests that focus on update/delete.
    """
    containers = [
        DeploymentContainerCreate(name="worker", image="worker:latest", role="app"),
    ]
    deployment_in = DeploymentCreate(
        containers=containers, network_name="fixture-net"
    )
    return await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )


# ---------------------------------------------------------------------------
# Tests — create_multi_container_deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_multi_container_deployment_basic(
    db_session: AsyncSession, test_user
):
    """
    create_multi_container_deployment() must persist a Deployment row
    with the correct owner and return the hydrated ORM object.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="app", image="my-app:latest", role="app"),
    ]
    deployment_in = DeploymentCreate(containers=containers, network_name="basic-net")

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert
    assert deployment is not None
    assert deployment.id is not None
    assert deployment.user_id == test_user.id

    # Verify it is persisted by re-fetching
    result = await db_session.execute(
        select(Deployment).filter(Deployment.id == deployment.id)
    )
    db_dep = result.scalars().first()
    assert db_dep is not None


@pytest.mark.asyncio
async def test_create_multi_container_deployment_generates_network_name(
    db_session: AsyncSession, test_user
):
    """
    When no network_name is supplied, create_multi_container_deployment()
    must auto-generate one with the 'net-' prefix and a unique hex suffix.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="srv", image="nginx:alpine", role="app"),
    ]
    deployment_in = DeploymentCreate(containers=containers, network_name=None)

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert
    assert deployment.network_name is not None
    assert deployment.network_name.startswith("net-")
    # Hex suffix should be 8 characters
    suffix = deployment.network_name[len("net-"):]
    assert len(suffix) == 8


@pytest.mark.asyncio
async def test_create_multi_container_deployment_uses_explicit_network_name(
    db_session: AsyncSession, test_user
):
    """
    When a custom network_name is supplied it must be stored as-is,
    without any modification.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="db", image="postgres:15-alpine", role="db"),
    ]
    deployment_in = DeploymentCreate(
        containers=containers, network_name="my-custom-net"
    )

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert
    assert deployment.network_name == "my-custom-net"


@pytest.mark.asyncio
async def test_create_multi_container_deployment_status_is_pending(
    db_session: AsyncSession, test_user
):
    """
    The Deployment itself must start in PENDING status.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="api", image="api:latest", role="api"),
    ]
    deployment_in = DeploymentCreate(containers=containers, network_name="status-net")

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert
    assert deployment.status == DeploymentStatus.PENDING


@pytest.mark.asyncio
async def test_create_multi_container_deployment_all_containers_are_pending(
    db_session: AsyncSession, test_user
):
    """
    Every DeploymentContainer created alongside the Deployment must start
    in PENDING status, regardless of the number of containers.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="app", image="my-app:latest", role="app"),
        DeploymentContainerCreate(name="db", image="postgres:15", role="db"),
        DeploymentContainerCreate(name="cache", image="redis:alpine", role="cache"),
    ]
    deployment_in = DeploymentCreate(
        containers=containers, network_name="multi-pending-net"
    )

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )
    deployment = await get_deployment(db_session, deployment.id)

    # Assert
    assert len(deployment.containers) == 3
    for container in deployment.containers:
        assert container.status == ContainerStatus.PENDING, (
            f"Container '{container.name}' should be PENDING but is {container.status}"
        )


@pytest.mark.asyncio
async def test_create_multi_container_deployment_containers_linked_to_deployment(
    db_session: AsyncSession, test_user
):
    """
    Each DeploymentContainer row must be bound to the parent Deployment via
    `deployment_id` and must store the correct image and name.
    """
    # Arrange
    containers = [
        DeploymentContainerCreate(name="web", image="nginx:latest", role="app"),
        DeploymentContainerCreate(name="datab", image="postgres:15", role="db"),
    ]
    deployment_in = DeploymentCreate(
        containers=containers, network_name="linked-net"
    )

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert — query DeploymentContainer directly
    result = await db_session.execute(
        select(DeploymentContainer).filter(
            DeploymentContainer.deployment_id == deployment.id
        )
    )
    db_containers = result.scalars().all()
    assert len(db_containers) == 2

    names = {c.name for c in db_containers}
    assert names == {"web", "datab"}

    for c in db_containers:
        assert c.deployment_id == deployment.id
        assert c.status == ContainerStatus.PENDING


@pytest.mark.asyncio
async def test_create_multi_container_deployment_git_url_label(
    db_session: AsyncSession, test_user
):
    """
    When a container specifies a git_url instead of an image, the stored
    image label must follow the convention `git:<git_url>`.
    """
    # Arrange
    git_url = "https://github.com/example/my-app"
    containers = [
        DeploymentContainerCreate(
            name="git-app", git_url=git_url, role="app"
        ),
    ]
    deployment_in = DeploymentCreate(containers=containers)

    # Act
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )

    # Assert
    result = await db_session.execute(
        select(DeploymentContainer).filter(
            DeploymentContainer.deployment_id == deployment.id
        )
    )
    container = result.scalars().first()
    assert container is not None
    assert container.image == f"git:{git_url}"


# ---------------------------------------------------------------------------
# Tests — get_deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deployment_returns_correct_deployment(
    db_session: AsyncSession, single_container_deployment
):
    """
    get_deployment() must return the Deployment with the specified id,
    including its containers via eager loading.
    """
    # Act
    dep = await get_deployment(db_session, single_container_deployment.id)

    # Assert
    assert dep is not None
    assert dep.id == single_container_deployment.id
    assert len(dep.containers) == 1


@pytest.mark.asyncio
async def test_get_deployment_returns_none_for_unknown_id(db_session: AsyncSession):
    """
    get_deployment() must return None for a UUID that has no corresponding row.
    """
    # Act
    result = await get_deployment(db_session, uuid.uuid4())

    # Assert
    assert result is None


# ---------------------------------------------------------------------------
# Tests — update_deployment_status / update_container_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_deployment_status(
    db_session: AsyncSession, single_container_deployment
):
    """
    update_deployment_status() must persist the new status to the database
    and return the updated Deployment object.
    """
    # Arrange — deployment starts as PENDING
    dep_id = single_container_deployment.id

    # Act
    updated = await update_deployment_status(
        db_session, deployment_id=dep_id, status=DeploymentStatus.RUNNING
    )

    # Assert
    assert updated is not None
    assert updated.status == DeploymentStatus.RUNNING

    # Verify in DB
    refetched = await get_deployment(db_session, dep_id)
    assert refetched.status == DeploymentStatus.RUNNING


@pytest.mark.asyncio
async def test_update_container_status_and_port(
    db_session: AsyncSession, single_container_deployment
):
    """
    update_container_status() must persist ContainerStatus, the Docker
    container ID, and the host port to the database.
    """
    # Arrange
    container = single_container_deployment.containers[0]

    # Act
    updated_container = await update_container_status(
        db_session,
        container_db_id=container.id,
        status=ContainerStatus.RUNNING,
        docker_container_id="docker_abc123",
        host_port=32768,
    )

    # Assert
    assert updated_container is not None
    assert updated_container.status == ContainerStatus.RUNNING
    assert updated_container.container_id == "docker_abc123"
    assert updated_container.host_port == 32768

    # Verify directly in DB
    result = await db_session.execute(
        select(DeploymentContainer).filter(
            DeploymentContainer.id == container.id
        )
    )
    db_con = result.scalars().first()
    assert db_con.status == ContainerStatus.RUNNING
    assert db_con.container_id == "docker_abc123"
    assert db_con.host_port == 32768


# ---------------------------------------------------------------------------
# Tests — delete_deployment (cascade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_deployment_removes_deployment_row(
    db_session: AsyncSession, single_container_deployment
):
    """
    delete_deployment() must remove the Deployment row from the database.
    get_deployment() must subsequently return None.
    """
    # Arrange
    dep_id = single_container_deployment.id

    # Act
    await delete_deployment(db_session, dep_id)

    # Assert
    deleted = await get_deployment(db_session, dep_id)
    assert deleted is None


@pytest.mark.asyncio
async def test_delete_deployment_cascades_to_containers(
    db_session: AsyncSession, test_user
):
    """
    Deleting a Deployment must CASCADE and remove all associated
    DeploymentContainer rows (ON DELETE CASCADE on deployment_id FK).
    """
    # Arrange — deployment with multiple containers
    containers = [
        DeploymentContainerCreate(name="svc-a", image="svc-a:latest", role="app"),
        DeploymentContainerCreate(name="svc-b", image="svc-b:latest", role="app"),
    ]
    deployment_in = DeploymentCreate(
        containers=containers, network_name="cascade-test-net"
    )
    deployment = await create_multi_container_deployment(
        db_session, user_id=test_user.id, deployment_in=deployment_in
    )
    deployment_id = deployment.id

    # Verify containers exist before delete
    res = await db_session.execute(
        select(DeploymentContainer).filter(
            DeploymentContainer.deployment_id == deployment_id
        )
    )
    assert len(res.scalars().all()) == 2

    # Act
    await delete_deployment(db_session, deployment_id)

    # Assert — deployment is gone
    deleted_dep = await get_deployment(db_session, deployment_id)
    assert deleted_dep is None

    # Assert — containers are gone
    res = await db_session.execute(
        select(DeploymentContainer).filter(
            DeploymentContainer.deployment_id == deployment_id
        )
    )
    assert len(res.scalars().all()) == 0


@pytest.mark.asyncio
async def test_delete_deployment_returns_none_for_unknown_id(db_session: AsyncSession):
    """
    Calling delete_deployment() with a non-existent UUID must return None
    without raising an exception.
    """
    # Act
    result = await delete_deployment(db_session, uuid.uuid4())

    # Assert — does not raise, returns None
    assert result is None
