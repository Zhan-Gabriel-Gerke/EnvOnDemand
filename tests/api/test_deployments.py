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
