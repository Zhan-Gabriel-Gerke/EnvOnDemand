"""
tests/api/test_deployments.py
==============================
Integration tests for POST /api/deployments.

Covers:
  - HTTP 401 when no auth token is provided.
  - HTTP 422 when the container list is empty.
  - HTTP 422 when a container has neither image nor git_url.
  - HTTP 422 when a container has both image and git_url.
  - HTTP 429 when the user's quota is exhausted.
  - HTTP 202 on a valid request (mem_limit accepted, response body correct).
  - Background task is enqueued but NOT awaited — response is immediate.

All tests hit the real test DB via `db_session`.
The Docker daemon is NOT called; the background task is submitted to
FastAPI's BackgroundTasks but never awaited during request handling.
"""
import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.security import create_access_token
from app.crud.user import create_user
from app.models.models import User, UserQuota
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def dep_user(db_session: AsyncSession) -> User:
    """Create a developer user that owns the test deployments."""
    user_in = UserCreate(
        username="depuser",
        email="dep@test.com",
        password="dep_password",
    )
    return await create_user(db_session, user_in=user_in)


@pytest_asyncio.fixture
def dep_auth_headers(dep_user: User) -> dict:
    """
    Authorization header for dep_user, built with the correct
    `data={"sub": ...}` call signature of create_access_token.
    """
    token = create_access_token(data={"sub": str(dep_user.id)})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helper — minimal valid single-container payload
# ---------------------------------------------------------------------------

def _valid_payload(*, network_name: str = "test-net", mem_limit: str = "512m") -> dict:
    """Return a valid DeploymentCreate JSON payload."""
    return {
        "network_name": network_name,
        "containers": [
            {
                "name": "app",
                "role": "app",
                "image": "nginx:latest",
                "mem_limit": mem_limit,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests — authentication guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deployment_unauthenticated_returns_401(client: AsyncClient):
    """
    POST /api/deployments without Authorization header must return HTTP 401.
    The global `get_current_user` dependency guards the /api router.
    """
    # Act
    response = await client.post("/api/deployments", json=_valid_payload())

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_deployment_expired_token_returns_401(client: AsyncClient):
    """
    A token with a negative TTL (already expired) must be rejected with HTTP 401.
    """
    from datetime import timedelta

    # Arrange — create a token that expired instantly
    expired_token = create_access_token(
        data={"sub": "00000000-0000-0000-0000-000000000001"},
        expires_delta=timedelta(seconds=-1),
    )
    headers = {"Authorization": f"Bearer {expired_token}"}

    # Act
    response = await client.post("/api/deployments", json=_valid_payload(), headers=headers)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Tests — payload validation (HTTP 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deployment_empty_containers_returns_422(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    A DeploymentCreate with an empty containers list must be rejected by
    Pydantic's @field_validator with HTTP 422.
    """
    # Arrange
    payload = {"network_name": "test-net", "containers": []}

    # Act
    response = await client.post(
        "/api/deployments", json=payload, headers=dep_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_create_deployment_missing_image_and_git_url_returns_422(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    A container spec with neither `image` nor `git_url` must be rejected
    by the DeploymentContainerCreate model_validator with HTTP 422.
    """
    # Arrange
    payload = {
        "network_name": "test-net",
        "containers": [
            {"name": "app", "role": "app"}  # missing image AND git_url
        ],
    }

    # Act
    response = await client.post(
        "/api/deployments", json=payload, headers=dep_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_create_deployment_both_image_and_git_url_returns_422(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    Providing both `image` and `git_url` on the same container spec violates
    the mutual-exclusivity rule and must produce HTTP 422.
    """
    # Arrange
    payload = {
        "network_name": "test-net",
        "containers": [
            {
                "name": "app",
                "role": "app",
                "image": "nginx:latest",
                "git_url": "https://github.com/example/repo",
            }
        ],
    }

    # Act
    response = await client.post(
        "/api/deployments", json=payload, headers=dep_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_create_deployment_missing_name_returns_422(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    A container spec without the required `name` field must fail validation.
    """
    # Arrange
    payload = {
        "network_name": "test-net",
        "containers": [
            {"role": "app", "image": "nginx:latest"}  # missing 'name'
        ],
    }

    # Act
    response = await client.post(
        "/api/deployments", json=payload, headers=dep_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Tests — quota enforcement (HTTP 429)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deployment_quota_exhausted_returns_429(
    client: AsyncClient,
    dep_user: User,
    dep_auth_headers: dict,
    db_session: AsyncSession,
):
    """
    When active_containers == max_containers the endpoint must refuse new
    deployments with HTTP 429 Too Many Requests.

    The quota is saturated by directly writing to the DB (no background task
    invoked), simulating a state where all slots are in use.
    """
    # Arrange — fill up the quota completely
    res = await db_session.execute(
        select(UserQuota).filter(UserQuota.user_id == dep_user.id)
    )
    quota = res.scalars().first()
    assert quota is not None, "UserQuota must be auto-provisioned on user creation"
    quota.active_containers = quota.max_containers  # 3/3 used
    await db_session.commit()

    # Act — try to request even 1 more container
    response = await client.post(
        "/api/deployments",
        json=_valid_payload(),
        headers=dep_auth_headers,
    )

    # Assert
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    detail = response.json()["detail"]
    assert "Resource limits exhausted" in detail


@pytest.mark.asyncio
async def test_create_deployment_quota_partially_used_allows_request(
    client: AsyncClient,
    dep_user: User,
    dep_auth_headers: dict,
    db_session: AsyncSession,
):
    """
    When some quota slots are used but enough remain, the request must succeed
    with HTTP 202.

    This test does NOT block on the background task — the task is submitted but
    never awaited, so no Docker calls occur.
    """
    # Arrange — use 2 out of 3 slots (1 slot remains)
    res = await db_session.execute(
        select(UserQuota).filter(UserQuota.user_id == dep_user.id)
    )
    quota = res.scalars().first()
    quota.active_containers = 2  # 2/3 used → 1 slot free
    await db_session.commit()

    # Act
    response = await client.post(
        "/api/deployments",
        json=_valid_payload(network_name="partial-quota-net"),
        headers=dep_auth_headers,
    )

    # Assert
    assert response.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.asyncio
async def test_create_deployment_requesting_more_than_available_slots_returns_429(
    client: AsyncClient,
    dep_user: User,
    dep_auth_headers: dict,
    db_session: AsyncSession,
):
    """
    Requesting 2 containers when only 1 slot is free must be rejected with
    HTTP 429 even though the user still has some quota remaining.
    """
    # Arrange — 2/3 slots used (only 1 free)
    res = await db_session.execute(
        select(UserQuota).filter(UserQuota.user_id == dep_user.id)
    )
    quota = res.scalars().first()
    quota.active_containers = 2
    await db_session.commit()

    # Act — request 2 containers
    payload = {
        "network_name": "over-quota-net",
        "containers": [
            {"name": "app1", "role": "app", "image": "nginx:latest"},
            {"name": "app2", "role": "app", "image": "nginx:latest"},
        ],
    }
    response = await client.post(
        "/api/deployments",
        json=payload,
        headers=dep_auth_headers,
    )

    # Assert
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


# ---------------------------------------------------------------------------
# Tests — successful creation (HTTP 202)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deployment_success_returns_202(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    A valid deployment request must return HTTP 202 Accepted immediately,
    without waiting for the background Docker task to complete.
    """
    # Act
    response = await client.post(
        "/api/deployments",
        json=_valid_payload(network_name="success-net"),
        headers=dep_auth_headers,
    )

    # Assert — accepted immediately
    assert response.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.asyncio
async def test_create_deployment_success_response_has_correct_schema(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    The 202 response body must conform to DeploymentRead: it must contain
    `id`, `user_id`, `status` == 'PENDING', `network_name`, and `containers`.
    """
    # Act
    response = await client.post(
        "/api/deployments",
        json=_valid_payload(network_name="schema-check-net"),
        headers=dep_auth_headers,
    )
    data = response.json()

    # Assert — required fields exist with correct types/values
    assert "id" in data
    assert "user_id" in data
    assert data["status"] == "PENDING"
    assert "network_name" in data
    assert data["network_name"] == "schema-check-net"
    assert "containers" in data
    assert isinstance(data["containers"], list)
    assert len(data["containers"]) == 1


@pytest.mark.asyncio
async def test_create_deployment_container_status_is_pending(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    Containers in the 202 response must have status == 'PENDING' because
    the background task has not yet touched Docker.
    """
    # Act
    response = await client.post(
        "/api/deployments",
        json=_valid_payload(network_name="pending-container-net"),
        headers=dep_auth_headers,
    )
    data = response.json()

    # Assert
    assert response.status_code == status.HTTP_202_ACCEPTED
    for container in data["containers"]:
        assert container["status"] == "PENDING"


@pytest.mark.asyncio
async def test_create_deployment_mem_limit_accepted(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    The `mem_limit` field on a container spec must be accepted by the API
    without any validation error. The value is stored and passed through
    to the Docker service by the background task.
    """
    # Arrange — use a non-default mem_limit
    payload = {
        "network_name": "mem-limit-net",
        "containers": [
            {
                "name": "limited",
                "role": "app",
                "image": "nginx:latest",
                "mem_limit": "256m",
            }
        ],
    }

    # Act
    response = await client.post(
        "/api/deployments",
        json=payload,
        headers=dep_auth_headers,
    )

    # Assert — accepted and status is PENDING (no 422 from schema)
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["status"] == "PENDING"


@pytest.mark.asyncio
async def test_create_deployment_auto_generates_network_name(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    When `network_name` is omitted, the API must auto-generate one.
    The response must carry a non-null `network_name`.
    """
    # Arrange — omit network_name
    payload = {
        "containers": [
            {"name": "srv", "role": "app", "image": "nginx:latest"}
        ]
    }

    # Act
    response = await client.post(
        "/api/deployments",
        json=payload,
        headers=dep_auth_headers,
    )

    # Assert
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["network_name"] is not None


@pytest.mark.asyncio
async def test_create_deployment_with_git_url_returns_202(
    client: AsyncClient, dep_auth_headers: dict
):
    """
    A deployment that uses git_url instead of image must also return 202.
    The background task handles the build; the API layer must not block on it.
    """
    # Arrange
    payload = {
        "network_name": "git-deploy-net",
        "containers": [
            {
                "name": "git-app",
                "role": "app",
                "git_url": "https://github.com/example/my-service",
            }
        ],
    }

    # Act
    response = await client.post(
        "/api/deployments",
        json=payload,
        headers=dep_auth_headers,
    )

    # Assert
    assert response.status_code == status.HTTP_202_ACCEPTED
    data = response.json()
    assert data["status"] == "PENDING"
    # The stored image label for git-sourced containers
    assert data["containers"][0]["image"].startswith("git:")


# --- Merged from test_deployments_extra.py ---

import uuid
import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.security import create_access_token
from app.crud.user import create_user
from app.crud.deployment import create_multi_container_deployment
from app.models.models import User, Deployment, DeploymentStatus
from app.schemas.user import UserCreate
from app.schemas.deployment import DeploymentCreate, DeploymentContainerCreate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="dep_owner", email="dep_owner@test.com", password="pw"),
    )


@pytest_asyncio.fixture
async def other_user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="dep_other", email="dep_other@test.com", password="pw"),
    )


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def deployment(db_session: AsyncSession, owner: User) -> Deployment:
    """A PENDING deployment with a single image-based container owned by `owner`."""
    dep_in = DeploymentCreate(
        network_name=f"test-extra-{uuid.uuid4().hex[:6]}",
        containers=[
            DeploymentContainerCreate(name="svc", role="app", image="nginx:latest")
        ],
    )
    return await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)


# ---------------------------------------------------------------------------
# list_deployments  GET /deployments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_deployments_returns_own(
    client: AsyncClient, owner: User, deployment: Deployment
):
    resp = await client.get("/api/deployments", headers=_auth(owner))
    assert resp.status_code == status.HTTP_200_OK
    ids = [d["id"] for d in resp.json()]
    assert str(deployment.id) in ids


# ---------------------------------------------------------------------------
# get_deployment  GET /deployments/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_deployment_found(
    client: AsyncClient, owner: User, deployment: Deployment
):
    resp = await client.get(f"/api/deployments/{deployment.id}", headers=_auth(owner))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["id"] == str(deployment.id)


@pytest.mark.asyncio
async def test_get_deployment_not_found_404(client: AsyncClient, owner: User):
    resp = await client.get(f"/api/deployments/{uuid.uuid4()}", headers=_auth(owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_deployment_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    resp = await client.get(f"/api/deployments/{deployment.id}", headers=_auth(other_user))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# delete_deployment  DELETE /deployments/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_deployment_not_found_404(client: AsyncClient, owner: User):
    resp = await client.delete(f"/api/deployments/{uuid.uuid4()}", headers=_auth(owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_deployment_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    resp = await client.delete(f"/api/deployments/{deployment.id}", headers=_auth(other_user))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_delete_deployment_success_204(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    """Delete a deployment whose containers have no Docker container_id (no real Docker call)."""
    dep_in = DeploymentCreate(
        network_name=f"delete-me-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="x", role="app", image="busybox")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    resp = await client.delete(f"/api/deployments/{dep.id}", headers=_auth(owner))
    assert resp.status_code == status.HTTP_204_NO_CONTENT


# ---------------------------------------------------------------------------
# stop_deployment  POST /deployments/{id}/stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_deployment_not_found_404(client: AsyncClient, owner: User):
    resp = await client.post(f"/api/deployments/{uuid.uuid4()}/stop", headers=_auth(owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_stop_deployment_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    resp = await client.post(f"/api/deployments/{deployment.id}/stop", headers=_auth(other_user))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_stop_deployment_already_stopped(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    """A STOPPED deployment returns 200 with 'Already stopped.' immediately."""
    dep_in = DeploymentCreate(
        network_name=f"stopped-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="s", role="app", image="busybox")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.STOPPED
    await db_session.commit()

    resp = await client.post(f"/api/deployments/{dep.id}/stop", headers=_auth(owner))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["message"] == "Already stopped."


@pytest.mark.asyncio
async def test_stop_deployment_success(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    """Stop a RUNNING deployment whose containers have no Docker container_id."""
    dep_in = DeploymentCreate(
        network_name=f"stop-ok-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="s", role="app", image="busybox")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.RUNNING
    await db_session.commit()

    # Containers have no container_id, so DockerService is opened but no stop call made
    with patch(
        "app.api.endpoints.deployments.DockerService",
        return_value=_async_ctx_mock(),
    ):
        resp = await client.post(f"/api/deployments/{dep.id}/stop", headers=_auth(owner))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["message"] == "Deployment stopped."


# ---------------------------------------------------------------------------
# start_deployment  POST /deployments/{id}/start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_deployment_not_found_404(client: AsyncClient, owner: User):
    resp = await client.post(f"/api/deployments/{uuid.uuid4()}/start", headers=_auth(owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_start_deployment_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    resp = await client.post(f"/api/deployments/{deployment.id}/start", headers=_auth(other_user))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_start_deployment_already_running(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    dep_in = DeploymentCreate(
        network_name=f"running-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="r", role="app", image="busybox")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.RUNNING
    await db_session.commit()

    resp = await client.post(f"/api/deployments/{dep.id}/start", headers=_auth(owner))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["message"] == "Already running."


@pytest.mark.asyncio
async def test_start_deployment_success(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    dep_in = DeploymentCreate(
        network_name=f"start-ok-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="s", role="app", image="busybox")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.STOPPED
    await db_session.commit()

    with patch(
        "app.api.endpoints.deployments.DockerService",
        return_value=_async_ctx_mock(),
    ):
        resp = await client.post(f"/api/deployments/{dep.id}/start", headers=_auth(owner))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["message"] == "Deployment started."


# ---------------------------------------------------------------------------
# get_deployment_logs  GET /deployments/{id}/logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_logs_not_found_404(client: AsyncClient, owner: User):
    resp = await client.get(f"/api/deployments/{uuid.uuid4()}/logs", headers=_auth(owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_logs_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    resp = await client.get(f"/api/deployments/{deployment.id}/logs", headers=_auth(other_user))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_get_logs_container_no_docker_id(
    client: AsyncClient, owner: User, deployment: Deployment
):
    """Container without a docker container_id → returns phase / error info, no Docker call."""
    mock_ds = _async_ctx_mock()
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ds):
        resp = await client.get(
            f"/api/deployments/{deployment.id}/logs", headers=_auth(owner)
        )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "logs" in data
    assert "svc" in data["logs"]


@pytest.mark.asyncio
async def test_get_logs_with_docker_id(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    """Container WITH a docker container_id → DockerService.get_container_logs is called."""
    dep_in = DeploymentCreate(
        network_name=f"logs-docker-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="web", role="app", image="nginx:latest")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    # Manually set a fake container_id
    dep.containers[0].container_id = "deadbeef1234"
    await db_session.commit()

    mock_ds = _async_ctx_mock(log_output="SOME LOG LINE")
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ds):
        resp = await client.get(
            f"/api/deployments/{dep.id}/logs", headers=_auth(owner)
        )

    assert resp.status_code == status.HTTP_200_OK
    assert "web" in resp.json()["logs"]


# ---------------------------------------------------------------------------
# update_deployment  PUT /deployments/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_deployment_not_found_404(client: AsyncClient, owner: User):
    payload = {
        "containers": [{"name": "a", "role": "app", "image": "nginx:latest"}]
    }
    resp = await client.put(
        f"/api/deployments/{uuid.uuid4()}", json=payload, headers=_auth(owner)
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_update_deployment_other_user_403(
    client: AsyncClient, other_user: User, deployment: Deployment
):
    payload = {
        "containers": [{"name": "a", "role": "app", "image": "nginx:latest"}]
    }
    resp = await client.put(
        f"/api/deployments/{deployment.id}", json=payload, headers=_auth(other_user)
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_update_deployment_success_202(
    client: AsyncClient, owner: User, db_session: AsyncSession
):
    """Update a deployment whose containers have no Docker container_id → no real Docker call."""
    dep_in = DeploymentCreate(
        network_name=f"update-ok-{uuid.uuid4().hex[:6]}",
        containers=[DeploymentContainerCreate(name="old", role="app", image="nginx:latest")],
    )
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)

    new_payload = {
        "network_name": dep.network_name,
        "containers": [{"name": "new", "role": "app", "image": "busybox"}],
    }
    with patch(
        "app.api.endpoints.deployments.DockerService",
        return_value=_async_ctx_mock(),
    ):
        resp = await client.put(
            f"/api/deployments/{dep.id}", json=new_payload, headers=_auth(owner)
        )

    assert resp.status_code == status.HTTP_202_ACCEPTED


# ---------------------------------------------------------------------------
# Helper — async context manager mock for DockerService
# ---------------------------------------------------------------------------

def _async_ctx_mock(log_output: str = ""):
    """Return a mock that works as `async with DockerService() as ds:`."""
    ds = MagicMock()
    ds.stop_container = AsyncMock()
    ds.remove_container = AsyncMock()
    ds.remove_network = AsyncMock()
    ds.start_container = AsyncMock()
    ds.get_container_logs = AsyncMock(return_value=log_output)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=ds)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ===========================================================================
# Background task unit tests (_wait_for_port, _deploy_single_container,
# run_multi_container_deployment) — all branches, no Docker or DB needed.
# ===========================================================================

from app.services.docker_service import DockerServiceError, GitCloneError, ContainerStartError

# ---------------------------------------------------------------------------
# _wait_for_port
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wait_for_port_returns_true_on_open_connection():
    from app.api.endpoints.deployments import _wait_for_port
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    with patch("app.api.endpoints.deployments.asyncio.open_connection", return_value=(AsyncMock(), mock_writer)):
        assert await _wait_for_port(port=8080, host="127.0.0.1", timeout=5) is True

@pytest.mark.asyncio
async def test_wait_for_port_returns_false_on_timeout():
    from app.api.endpoints.deployments import _wait_for_port
    with patch("app.api.endpoints.deployments.asyncio.open_connection", side_effect=ConnectionRefusedError):
        with patch("app.api.endpoints.deployments.asyncio.sleep", new=AsyncMock()):
            assert await _wait_for_port(port=9, host="127.0.0.1", timeout=0) is False

# ---------------------------------------------------------------------------
# _deploy_single_container
# ---------------------------------------------------------------------------

def _mock_crud():
    m = MagicMock()
    m.update_container_status = AsyncMock()
    return m

@pytest.mark.asyncio
async def test_deploy_single_container_image_success():
    from app.api.endpoints.deployments import _deploy_single_container
    mock_ds = MagicMock()
    mock_ds.run_container = AsyncMock(return_value={"container_id": "abc", "port": 9000, "ip": "172.17.0.2"})
    spec = DeploymentContainerCreate(name="web", role="app", image="nginx:latest")
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds, AsyncMock(), uuid.uuid4(), spec, "net") is True

@pytest.mark.asyncio
async def test_deploy_single_container_git_success():
    from app.api.endpoints.deployments import _deploy_single_container
    mock_ds = MagicMock()
    mock_ds.clone_repo = AsyncMock(return_value="/tmp/repo")
    mock_ds.build_image = AsyncMock(return_value="build ok")
    mock_ds.cleanup_repo = MagicMock()
    mock_ds.run_container = AsyncMock(return_value={"container_id": "xyz", "port": 5000, "ip": "172.17.0.3"})
    spec = DeploymentContainerCreate(name="app", role="app", git_url="https://github.com/x/y")
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds, AsyncMock(), uuid.uuid4(), spec, "net") is True

@pytest.mark.asyncio
async def test_deploy_single_container_errors():
    from app.api.endpoints.deployments import _deploy_single_container
    spec_git = DeploymentContainerCreate(name="app", role="app", git_url="https://bad/repo")
    spec_img = DeploymentContainerCreate(name="web", role="app", image="nginx:latest")
    
    mock_ds_clone = MagicMock()
    mock_ds_clone.clone_repo = AsyncMock(side_effect=GitCloneError("clone failed"))
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds_clone, AsyncMock(), uuid.uuid4(), spec_git, "net") is False

    mock_ds_build = MagicMock()
    mock_ds_build.clone_repo = AsyncMock(return_value="/tmp/repo")
    mock_ds_build.build_image = AsyncMock(side_effect=ContainerStartError("build failed"))
    mock_ds_build.cleanup_repo = MagicMock()
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds_build, AsyncMock(), uuid.uuid4(), spec_git, "net") is False

    mock_ds_run = MagicMock()
    mock_ds_run.run_container = AsyncMock(side_effect=DockerServiceError("daemon down"))
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds_run, AsyncMock(), uuid.uuid4(), spec_img, "net") is False

    mock_ds_generic = MagicMock()
    mock_ds_generic.run_container = AsyncMock(side_effect=RuntimeError("kernel panic"))
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        assert await _deploy_single_container(mock_ds_generic, AsyncMock(), uuid.uuid4(), spec_img, "net") is False

@pytest.mark.asyncio
async def test_deploy_single_container_healthcheck_fails():
    from app.api.endpoints.deployments import _deploy_single_container
    mock_ds = MagicMock()
    mock_ds.run_container = AsyncMock(return_value={"container_id": "abc", "port": 3000, "ip": "10.0.0.1"})
    mock_ds.get_container_logs = AsyncMock(return_value="crash log")
    spec = DeploymentContainerCreate(name="web", role="app", image="nginx:latest", ports={3000: 3000})
    with patch("app.api.endpoints.deployments.crud", _mock_crud()):
        with patch("app.api.endpoints.deployments._wait_for_port", new=AsyncMock(return_value=False)):
            assert await _deploy_single_container(mock_ds, AsyncMock(), uuid.uuid4(), spec, "net") is False

# ---------------------------------------------------------------------------
# run_multi_container_deployment
# ---------------------------------------------------------------------------

def _bg_session_ctx():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx

def _bg_docker_ctx():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx

@pytest.mark.asyncio
async def test_run_multi_container_success():
    from app.api.endpoints.deployments import run_multi_container_deployment
    dep_id = uuid.uuid4()
    mock_c = MagicMock(); mock_c.id = uuid.uuid4(); mock_c.name = "web"
    mock_dep = MagicMock(); mock_dep.network_name = "net"; mock_dep.containers = [mock_c]
    mock_crud = MagicMock()
    mock_crud.get_deployment = AsyncMock(return_value=mock_dep)
    mock_crud.update_deployment_status = AsyncMock()
    mock_crud.update_container_status = AsyncMock()
    spec = DeploymentContainerCreate(name="web", role="app", image="nginx:latest")
    
    with patch("app.api.endpoints.deployments.AsyncSessionLocal", return_value=_bg_session_ctx()), \
         patch("app.api.endpoints.deployments.DockerService", return_value=_bg_docker_ctx()), \
         patch("app.api.endpoints.deployments.crud", mock_crud), \
         patch("app.api.endpoints.deployments._deploy_single_container", new=AsyncMock(return_value=True)):
        await run_multi_container_deployment(dep_id, [spec])
    mock_crud.update_deployment_status.assert_called()

@pytest.mark.asyncio
async def test_run_multi_container_failures():
    from app.api.endpoints.deployments import run_multi_container_deployment
    dep_id = uuid.uuid4()
    mock_crud = MagicMock()
    
    # Not found
    mock_crud.get_deployment = AsyncMock(return_value=None)
    mock_crud.update_deployment_status = AsyncMock()
    with patch("app.api.endpoints.deployments.AsyncSessionLocal", return_value=_bg_session_ctx()), \
         patch("app.api.endpoints.deployments.DockerService", return_value=_bg_docker_ctx()), \
         patch("app.api.endpoints.deployments.crud", mock_crud):
        await run_multi_container_deployment(dep_id, [])
    mock_crud.update_deployment_status.assert_not_called()

    # ConnectionError
    mock_crud.get_deployment = AsyncMock(side_effect=ConnectionError("daemon down"))
    with patch("app.api.endpoints.deployments.AsyncSessionLocal", return_value=_bg_session_ctx()), \
         patch("app.api.endpoints.deployments.DockerService", return_value=_bg_docker_ctx()), \
         patch("app.api.endpoints.deployments.crud", mock_crud):
        await run_multi_container_deployment(dep_id, [])
    mock_crud.update_deployment_status.assert_called_once()

@pytest.mark.asyncio
async def test_run_multi_container_dependency_fails():
    from app.api.endpoints.deployments import run_multi_container_deployment
    dep_id = uuid.uuid4()
    mock_a = MagicMock(); mock_a.id = uuid.uuid4(); mock_a.name = "db"
    mock_b = MagicMock(); mock_b.id = uuid.uuid4(); mock_b.name = "app"
    mock_dep = MagicMock(); mock_dep.network_name = "net"; mock_dep.containers = [mock_a, mock_b]
    mock_crud = MagicMock()
    mock_crud.get_deployment = AsyncMock(return_value=mock_dep)
    mock_crud.update_deployment_status = AsyncMock()
    mock_crud.update_container_status = AsyncMock()
    spec_a = DeploymentContainerCreate(name="db", role="db", image="postgres:15")
    spec_b = DeploymentContainerCreate(name="app", role="app", image="myapp", depends_on=["db"])

    with patch("app.api.endpoints.deployments.AsyncSessionLocal", return_value=_bg_session_ctx()), \
         patch("app.api.endpoints.deployments.DockerService", return_value=_bg_docker_ctx()), \
         patch("app.api.endpoints.deployments.crud", mock_crud), \
         patch("app.api.endpoints.deployments._deploy_single_container", new=AsyncMock(return_value=False)):
        await run_multi_container_deployment(dep_id, [spec_a, spec_b])
    mock_crud.update_deployment_status.assert_called()



@pytest.mark.asyncio
async def test_create_deployment_integrity_error_400(client: AsyncClient, owner: User, db_session: AsyncSession):
    from sqlalchemy.exc import IntegrityError
    payload = {"containers": [{"name": "x", "role": "app", "image": "nginx:latest"}]}
    with patch("app.api.endpoints.deployments.crud.create_multi_container_deployment", new=AsyncMock(side_effect=IntegrityError("dup", {}, None))):
        resp = await client.post("/api/deployments", json=payload, headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST

@pytest.mark.asyncio
async def test_create_deployment_integrity_error_with_network_name_400(client: AsyncClient, owner: User):
    from sqlalchemy.exc import IntegrityError
    payload = {"network_name": "dup-net", "containers": [{"name": "x", "role": "app", "image": "nginx:latest"}]}
    with patch("app.api.endpoints.deployments.crud.create_multi_container_deployment", new=AsyncMock(side_effect=IntegrityError("dup", {}, None))):
        resp = await client.post("/api/deployments", json=payload, headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "dup-net" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_delete_deployment_with_container_ids(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"del-cid-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="c", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "fakeid123"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.remove_container = AsyncMock()
    ds.remove_network = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.delete(f"/api/deployments/{dep.id}", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_204_NO_CONTENT
    ds.remove_container.assert_called_once_with("fakeid123")

@pytest.mark.asyncio
async def test_delete_deployment_container_remove_error_still_204(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"del-err-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="c", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "fakeid456"
    await db_session.commit()
    from app.services.docker_service import DockerServiceError
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.remove_container = AsyncMock(side_effect=DockerServiceError("container gone"))
    ds.remove_network = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.delete(f"/api/deployments/{dep.id}", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_204_NO_CONTENT

@pytest.mark.asyncio
async def test_delete_deployment_network_remove_error_still_204(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"net-err-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="c", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "fakeid789"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.remove_container = AsyncMock()
    ds.remove_network = AsyncMock(side_effect=Exception("net gone"))
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.delete(f"/api/deployments/{dep.id}", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_204_NO_CONTENT

@pytest.mark.asyncio
async def test_stop_deployment_with_container_id(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"stop-cid-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="s", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.RUNNING
    dep.containers[0].container_id = "stopme123"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.stop_container = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.post(f"/api/deployments/{dep.id}/stop", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    ds.stop_container.assert_called_once_with("stopme123")

@pytest.mark.asyncio
async def test_stop_deployment_container_error_still_200(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"stop-err-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="s", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.RUNNING
    dep.containers[0].container_id = "stopfail"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.stop_container = AsyncMock(side_effect=Exception("daemon gone"))
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.post(f"/api/deployments/{dep.id}/stop", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK

@pytest.mark.asyncio
async def test_start_deployment_with_container_id(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"start-cid-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="s", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.STOPPED
    dep.containers[0].container_id = "startme123"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.start_container = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.post(f"/api/deployments/{dep.id}/start", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    ds.start_container.assert_called_once_with("startme123")

@pytest.mark.asyncio
async def test_start_deployment_container_error_still_200(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"start-err-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="s", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.status = DeploymentStatus.STOPPED
    dep.containers[0].container_id = "startfail"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.start_container = AsyncMock(side_effect=Exception("daemon gone"))
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.post(f"/api/deployments/{dep.id}/start", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK

@pytest.mark.asyncio
async def test_get_logs_container_has_last_error(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"logs-err-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="e", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].last_error = "OOM kill"
    await db_session.commit()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.get(f"/api/deployments/{dep.id}/logs", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    assert "OOM kill" in resp.json()["logs"]["e"]

@pytest.mark.asyncio
async def test_get_logs_container_has_build_logs(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"logs-build-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="b", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].build_logs = "Step 1/3: FROM alpine"
    await db_session.commit()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.get(f"/api/deployments/{dep.id}/logs", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    assert "Step 1/3" in resp.json()["logs"]["b"]

@pytest.mark.asyncio
async def test_get_logs_container_not_started_yet(client: AsyncClient, owner: User, deployment: Deployment):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.get(f"/api/deployments/{deployment.id}/logs", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    assert "not started yet" in resp.json()["logs"]["svc"]

@pytest.mark.asyncio
async def test_get_logs_docker_fetch_error(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"logs-fail-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="f", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "failid"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.get_container_logs = AsyncMock(side_effect=Exception("socket hang up"))
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.get(f"/api/deployments/{dep.id}/logs", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    assert "Could not retrieve logs" in resp.json()["logs"]["f"]

@pytest.mark.asyncio
async def test_get_logs_docker_id_with_last_error_overlay(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"logs-overlay-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="o", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "overlaycid"
    dep.containers[0].last_error = "startup crash"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.get_container_logs = AsyncMock(return_value="application log line")
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.get(f"/api/deployments/{dep.id}/logs", headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_200_OK
    logs_text = resp.json()["logs"]["o"]
    assert "startup crash" in logs_text
    assert "application log line" in logs_text

@pytest.mark.asyncio
async def test_update_deployment_quota_exceeded_429(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"upd-quota-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="a", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    owner.quota.active_containers = owner.quota.max_containers
    await db_session.commit()
    payload = {"network_name": dep.network_name, "containers": [{"name": "a", "role": "app", "image": "nginx:latest"}, {"name": "b", "role": "app", "image": "nginx:latest"}]}
    resp = await client.put(f"/api/deployments/{dep.id}", json=payload, headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS

@pytest.mark.asyncio
async def test_update_deployment_teardown_existing_containers(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"upd-teardown-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="old", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "teardownme"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.stop_container = AsyncMock()
    ds.remove_container = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    payload = {"network_name": dep.network_name, "containers": [{"name": "new", "role": "app", "image": "busybox"}]}
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.put(f"/api/deployments/{dep.id}", json=payload, headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_202_ACCEPTED
    ds.stop_container.assert_called_once_with("teardownme")
    ds.remove_container.assert_called_once_with("teardownme")

@pytest.mark.asyncio
async def test_update_deployment_teardown_error_swallowed(client: AsyncClient, owner: User, db_session: AsyncSession):
    dep_in = DeploymentCreate(network_name=f"upd-tearerr-{uuid.uuid4().hex[:6]}", containers=[DeploymentContainerCreate(name="old", role="app", image="nginx:latest")])
    dep = await create_multi_container_deployment(db_session, user_id=owner.id, deployment_in=dep_in)
    dep.containers[0].container_id = "tearfail"
    await db_session.commit()
    mock_ctx = MagicMock()
    ds = MagicMock()
    ds.stop_container = AsyncMock(side_effect=Exception("stop failed"))
    ds.remove_container = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=ds)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    payload = {"network_name": dep.network_name, "containers": [{"name": "new", "role": "app", "image": "busybox"}]}
    with patch("app.api.endpoints.deployments.DockerService", return_value=mock_ctx):
        resp = await client.put(f"/api/deployments/{dep.id}", json=payload, headers={"Authorization": f"Bearer {create_access_token(data={'sub': str(owner.id)})}"})
    assert resp.status_code == status.HTTP_202_ACCEPTED

