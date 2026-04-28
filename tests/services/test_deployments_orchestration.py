"""
tests/services/test_deployments_orchestration.py
=================================================
Tests for the `run_multi_container_deployment` background task
(app.api.endpoints.deployments).

Strategy
--------
* The real test database is used via the shared `db_session` fixture.
  Data committed by the test session is visible to the background task because
  both sessions connect to the same PostgreSQL instance.

* The Docker daemon is **never contacted**.  `DockerService` is patched with
  an AsyncMock that simulates run_container / build_and_run_from_git responses.

Key mock shape
--------------
`DockerService.run_container` must return:
    {"container_id": "<docker_id>", "port": <host_port>}

because `_deploy_single_container` reads:
    run_info.get("port")
    run_info["container_id"]

Not `"id"` or `"host_port"` — those are wrong.

Session isolation note
-----------------------
`run_multi_container_deployment` uses `AsyncSessionLocal()` — a **separate**
session from the test's `db_session`.  After the background task commits,
we call `db_session.expire_all()` to discard stale cached ORM state and
force a fresh DB read.

IMPORTANT: `deployment_id` must be captured as a plain UUID **before**
`expire_all()` is called, because expire_all() also expires the primary-key
attribute, making it inaccessible without an async await.

Dependency graph rules under test
-----------------------------------
Given containers [B, A] where A.depends_on = ["B"]:

  Scenario 1  B succeeds → A runs → deployment RUNNING
  Scenario 2  B fails   → A is skipped / marked FAILED → deployment FAILED
"""
import asyncio
import pytest
import pytest_asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.endpoints.deployments import run_multi_container_deployment
from app.crud.deployment import create_multi_container_deployment, get_deployment
from app.crud.user import create_user
from app.models.models import ContainerStatus, DeploymentStatus
from app.schemas.deployment import DeploymentContainerCreate, DeploymentCreate
from app.schemas.user import UserCreate
from app.services.docker_service import DockerService, DockerServiceError


# ---------------------------------------------------------------------------
# Helper — construct a complete DockerService mock usable as async ctxmgr
# ---------------------------------------------------------------------------


def _make_mock_docker_service():
    """
    Return (mock_constructor, mock_instance).

    `mock_constructor` replaces the `DockerService` class so that:

        async with DockerService() as svc:
            result = await svc.run_container(...)

    calls `mock_instance.run_container(...)`.
    """
    mock_instance = AsyncMock(spec=DockerService)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_constructor = MagicMock(return_value=mock_instance)
    return mock_constructor, mock_instance


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orch_user(db_session: AsyncSession):
    """Developer user whose deployments are tested in this module."""
    user_in = UserCreate(username="orch", email="orch@m.com", password="pwd")
    return await create_user(db_session, user_in=user_in)


# ---------------------------------------------------------------------------
# Helper — create deployment and return it + captured id
# ---------------------------------------------------------------------------


async def _create_deployment(db_session, user_id, containers_in, network_name):
    deployment_in = DeploymentCreate(
        containers=containers_in, network_name=network_name
    )
    return await create_multi_container_deployment(
        db_session, user_id=user_id, deployment_in=deployment_in
    )


# ---------------------------------------------------------------------------
# Helper — expire_all + re-fetch deployment with fresh data
# ---------------------------------------------------------------------------


async def _refresh_deployment(db_session: AsyncSession, deployment_id: uuid.UUID):
    """
    Expire the session identity map and re-fetch the deployment with fresh
    state from the database.  Call this after `run_multi_container_deployment`
    so the assertions see the committed state from the background task's
    separate session.
    """
    db_session.expire_all()
    return await get_deployment(db_session, deployment_id)


# ===========================================================================
#  1. Dependency graph — B fails → A aborts
# ===========================================================================


@pytest.mark.asyncio
async def test_dependency_graph_b_fails_a_is_aborted(
    db_session: AsyncSession, orch_user
):
    """
    Dependency graph: A depends_on B. B fails.

    Expected behaviour
    ------------------
    * DockerService.run_container is called exactly once (for B).
    * B's DB status → FAILED.
    * A's DB status → FAILED (skipped, never attempted).
    * Deployment status → FAILED.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="B", image="redis:alpine", role="db"),
        DeploymentContainerCreate(
            name="A", image="app:latest", role="app", depends_on=["B"]
        ),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "dep-graph-fail-net"
    )
    # Capture PK before expire_all() makes it inaccessible without an async load
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.side_effect = Exception("Docker daemon unreachable")

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    # Expire session cache and re-fetch fresh state
    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert — deployment is FAILED
    assert dep.status == DeploymentStatus.FAILED, f"Expected FAILED, got {dep.status}"

    # Assert — B failed
    con_b = next(c for c in dep.containers if c.name == "B")
    assert con_b.status == ContainerStatus.FAILED, (
        f"Container B should be FAILED, got {con_b.status}"
    )

    # Assert — A was skipped (dependency failed) → also FAILED
    con_a = next(c for c in dep.containers if c.name == "A")
    assert con_a.status == ContainerStatus.FAILED, (
        f"Container A should be FAILED (skipped), got {con_a.status}"
    )

    # Assert — Docker was called only once (for B, never for A)
    assert mock_ds.run_container.call_count == 1
    _args, kwargs = mock_ds.run_container.call_args
    assert kwargs.get("name") == "B"


# ===========================================================================
#  2. Dependency graph — B succeeds → A runs → RUNNING
# ===========================================================================


@pytest.mark.asyncio
async def test_dependency_graph_b_succeeds_a_runs(
    db_session: AsyncSession, orch_user
):
    """
    Dependency graph: A depends_on B. Both succeed.

    Expected behaviour
    ------------------
    * DockerService.run_container called twice (B then A).
    * B's DB status → RUNNING with assigned host_port.
    * A's DB status → RUNNING with assigned host_port.
    * Deployment status → RUNNING.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="B", image="redis:alpine", role="db"),
        DeploymentContainerCreate(
            name="A", image="api:latest", role="app", depends_on=["B"]
        ),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "dep-graph-ok-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.side_effect = [
        {"container_id": "b_docker_id", "port": 5432},   # B
        {"container_id": "a_docker_id", "port": 8080},   # A
    ]

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert
    assert dep.status == DeploymentStatus.RUNNING

    con_b = next(c for c in dep.containers if c.name == "B")
    assert con_b.status == ContainerStatus.RUNNING
    assert con_b.host_port == 5432

    con_a = next(c for c in dep.containers if c.name == "A")
    assert con_a.status == ContainerStatus.RUNNING
    assert con_a.host_port == 8080

    assert mock_ds.run_container.call_count == 2


# ===========================================================================
#  3. No dependencies — happy path (single container)
# ===========================================================================


@pytest.mark.asyncio
async def test_single_container_success(db_session: AsyncSession, orch_user):
    """
    Happy path with a single, dependency-free container.

    After run_multi_container_deployment completes:
    * The container is RUNNING with the correct host_port and docker container_id.
    * The deployment is RUNNING.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="web", image="nginx:latest", role="app"),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "single-ok-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.return_value = {
        "container_id": "nginx_container_abc",
        "port": 9090,
    }

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert
    assert dep.status == DeploymentStatus.RUNNING

    con = dep.containers[0]
    assert con.name == "web"
    assert con.status == ContainerStatus.RUNNING
    assert con.host_port == 9090
    assert con.container_id == "nginx_container_abc"


# ===========================================================================
#  4. Single container failure
# ===========================================================================


@pytest.mark.asyncio
async def test_single_container_docker_failure(db_session: AsyncSession, orch_user):
    """
    When DockerService.run_container raises an exception the container and
    deployment must both be marked FAILED.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="bad_svc", image="bad:image", role="app"),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "single-fail-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.side_effect = DockerServiceError("Image not found")

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert
    assert dep.status == DeploymentStatus.FAILED
    assert dep.containers[0].status == ContainerStatus.FAILED


# ===========================================================================
#  5. mem_limit is forwarded to DockerService.run_container
# ===========================================================================


@pytest.mark.asyncio
async def test_mem_limit_forwarded_to_docker_service(
    db_session: AsyncSession, orch_user
):
    """
    When a container spec has mem_limit set, that exact value must appear
    as the `mem_limit` keyword argument in the DockerService.run_container call.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(
            name="limited", image="nginx:latest", role="app", mem_limit="1024m"
        ),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "mem-limit-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.return_value = {
        "container_id": "limited_c_id",
        "port": 8000,
    }

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    await _refresh_deployment(db_session, deployment_id)

    # Assert — inspect call kwargs
    mock_ds.run_container.assert_called_once()
    _args, kwargs = mock_ds.run_container.call_args
    assert kwargs.get("mem_limit") == "1024m", (
        f"Expected mem_limit='1024m', got {kwargs.get('mem_limit')!r}"
    )
    assert kwargs.get("name") == "limited"
    assert kwargs.get("image_tag") == "nginx:latest"


# ===========================================================================
#  6. network_name is forwarded to DockerService.run_container
# ===========================================================================


@pytest.mark.asyncio
async def test_network_name_forwarded_to_docker_service(
    db_session: AsyncSession, orch_user
):
    """
    The deployment's network_name must be passed as the `network` kwarg to
    DockerService.run_container so all containers join the same Docker network.
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="api", image="api:latest", role="app"),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "network-pass-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.run_container.return_value = {
        "container_id": "api_c_id",
        "port": 3000,
    }

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    await _refresh_deployment(db_session, deployment_id)

    # Assert
    _args, kwargs = mock_ds.run_container.call_args
    assert kwargs.get("network") == "network-pass-net"


# ===========================================================================
#  7. Git-sourced container uses build_and_run_from_git
# ===========================================================================


@pytest.mark.asyncio
async def test_git_url_container_uses_build_and_run_from_git(
    db_session: AsyncSession, orch_user
):
    """
    When a container spec has `git_url` set, the orchestrator must call
    `DockerService.build_and_run_from_git` instead of `run_container`.
    """
    # Arrange
    git_url = "https://github.com/example/my-service"
    containers_in = [
        DeploymentContainerCreate(
            name="git-svc", git_url=git_url, role="app", mem_limit="512m"
        ),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "git-build-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    mock_ds.clone_repo.return_value = "/tmp/fake"
    mock_ds.build_image.return_value = "logs"
    mock_ds.run_container.return_value = {
        "container_id": "git_built_id",
        "port": 7000,
    }

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert — git clone and build were called
    mock_ds.clone_repo.assert_called_once_with(git_url)
    mock_ds.build_image.assert_called_once()
    mock_ds.run_container.assert_called_once()

    # Assert — mem_limit was forwarded to run_container
    _args, kwargs = mock_ds.run_container.call_args
    assert kwargs.get("mem_limit") == "512m"

    # Assert — DB state
    assert dep.status == DeploymentStatus.RUNNING
    assert dep.containers[0].host_port == 7000


# ===========================================================================
#  8. Non-existent deployment_id is handled gracefully
# ===========================================================================


@pytest.mark.asyncio
async def test_nonexistent_deployment_id_handled_gracefully(
    db_session: AsyncSession, orch_user
):
    """
    Passing a random UUID that has no Deployment row must not raise and
    must not attempt to touch Docker.
    """
    # Arrange
    fake_id = uuid.uuid4()
    containers_in = [
        DeploymentContainerCreate(name="x", image="nginx:latest", role="app"),
    ]

    mock_constructor, mock_ds = _make_mock_docker_service()

    # Act — must not raise
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=fake_id,
            container_specs=containers_in,
        )

    # Assert — Docker was never called
    mock_ds.run_container.assert_not_called()
    mock_ds.clone_repo.assert_not_called()


# ===========================================================================
#  9. Chain dependency: C → B → A (3-level)
# ===========================================================================


@pytest.mark.asyncio
async def test_three_level_dependency_chain_b_fails(
    db_session: AsyncSession, orch_user
):
    """
    Three-level chain: C depends_on B, B depends_on A.
    A succeeds, B fails — C must be aborted without ever being started.

    Deployment status must be FAILED.
    Docker must be called twice: once for A (success), once for B (fail).
    """
    # Arrange
    containers_in = [
        DeploymentContainerCreate(name="A", image="base:latest", role="db"),
        DeploymentContainerCreate(
            name="B", image="mid:latest", role="app", depends_on=["A"]
        ),
        DeploymentContainerCreate(
            name="C", image="top:latest", role="app", depends_on=["B"]
        ),
    ]
    deployment = await _create_deployment(
        db_session, orch_user.id, containers_in, "chain3-net"
    )
    deployment_id = deployment.id

    mock_constructor, mock_ds = _make_mock_docker_service()
    # A succeeds, B fails
    mock_ds.run_container.side_effect = [
        {"container_id": "a_id", "port": 5000},   # A
        Exception("B failed to start"),            # B
    ]

    # Act
    with patch("app.api.endpoints.deployments.DockerService", mock_constructor):
        await run_multi_container_deployment(
            deployment_id=deployment_id,
            container_specs=containers_in,
        )

    dep = await _refresh_deployment(db_session, deployment_id)

    # Assert
    assert dep.status == DeploymentStatus.FAILED

    con_a = next(c for c in dep.containers if c.name == "A")
    assert con_a.status == ContainerStatus.RUNNING

    con_b = next(c for c in dep.containers if c.name == "B")
    assert con_b.status == ContainerStatus.FAILED

    con_c = next(c for c in dep.containers if c.name == "C")
    assert con_c.status == ContainerStatus.FAILED

    # Docker called twice only (A and B — not C)
    assert mock_ds.run_container.call_count == 2
