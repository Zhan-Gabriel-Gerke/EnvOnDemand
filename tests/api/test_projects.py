"""
tests/api/test_projects.py
===========================
Integration tests for the /api/projects endpoints.

Auth / RBAC analysis
---------------------
In main.py the projects router is mounted with:

    app.include_router(
        projects.router,
        prefix="/api/projects",
        dependencies=[Depends(get_current_user)]
    )

There is NO RequireRole dependency on the projects router — any authenticated
user (regardless of role) can access the project endpoints.

Therefore the RBAC tests verify:
  1. Unauthenticated requests → HTTP 401 (bearer token missing).
  2. An invalid / tampered token → HTTP 401.
  3. A fully authenticated user (any role) can list projects → HTTP 200.
  4. Creating a project with a valid payload → HTTP 201.
  5. Creating a duplicate project (same owner + name) → HTTP 409.
  6. Fetching a non-existent project → HTTP 404.
  7. Deleting a non-existent project → HTTP 404.
"""
import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.crud.user import create_user
from app.models.models import User
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def proj_user(db_session: AsyncSession) -> User:
    """
    Standard developer user for project endpoint tests.
    Uses an isolated username/email to avoid collisions with conftest fixtures.
    """
    user_in = UserCreate(
        username="projuser",
        email="proj@test.com",
        password="proj_password",
    )
    return await create_user(db_session, user_in=user_in)


@pytest_asyncio.fixture
def proj_auth_headers(proj_user: User) -> dict:
    """
    Authorization header dict for proj_user.
    Uses the correct `data={"sub": ...}` call signature.
    """
    token = create_access_token(data={"sub": str(proj_user.id)})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests — authentication guard (HTTP 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_unauthenticated_returns_401(client: AsyncClient):
    """
    GET /api/projects without an Authorization header must return HTTP 401.
    The get_current_user dependency on the router rejects all unauthenticated calls.
    """
    # Act
    response = await client.get("/api/projects/")

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_project_unauthenticated_returns_401(client: AsyncClient):
    """
    POST /api/projects without a token must also return HTTP 401.
    """
    # Act
    response = await client.post(
        "/api/projects/",
        json={"name": "my-project", "description": "desc"},
    )

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_project_unauthenticated_returns_401(client: AsyncClient):
    """
    GET /api/projects/{id} without a token must return HTTP 401.
    """
    import uuid

    # Act
    response = await client.get(f"/api/projects/{uuid.uuid4()}")

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_delete_project_unauthenticated_returns_401(client: AsyncClient):
    """
    DELETE /api/projects/{id} without a token must return HTTP 401.
    """
    import uuid

    # Act
    response = await client.delete(f"/api/projects/{uuid.uuid4()}")

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client: AsyncClient):
    """
    A malformed / tampered Bearer token must be rejected with HTTP 401.
    """
    # Arrange — deliberately corrupt the token signature
    headers = {"Authorization": "Bearer this.is.not.a.valid.jwt"}

    # Act
    response = await client.get("/api/projects/", headers=headers)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Tests — authenticated access (any role is allowed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_authenticated_returns_200(
    client: AsyncClient, proj_auth_headers: dict
):
    """
    An authenticated developer can list projects even if none exist yet.
    The response must be HTTP 200 with an empty list body.
    """
    # Act
    response = await client.get("/api/projects/", headers=proj_auth_headers)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_project_authenticated_returns_201(
    client: AsyncClient,
    proj_user: User,
    proj_auth_headers: dict,
):
    """
    A POST /api/projects/ with a valid payload and a valid token must
    return HTTP 201 Created with the project data including an `id`.
    """
    # Arrange
    payload = {
        "name": "my-new-project",
        "description": "A test project",
        "owner_id": str(proj_user.id),
    }

    # Act
    response = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "my-new-project"
    assert "id" in data


@pytest.mark.asyncio
async def test_get_created_project_returns_200(
    client: AsyncClient,
    proj_user: User,
    proj_auth_headers: dict,
):
    """
    After creating a project, GET /api/projects/{id} must return the same
    project with HTTP 200.
    """
    # Arrange — create project first
    payload = {
        "name": "fetchable-project",
        "owner_id": str(proj_user.id),
    }
    create_response = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )
    assert create_response.status_code == 201
    project_id = create_response.json()["id"]

    # Act
    response = await client.get(
        f"/api/projects/{project_id}", headers=proj_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == project_id
    assert data["name"] == "fetchable-project"


@pytest.mark.asyncio
async def test_get_nonexistent_project_returns_404(
    client: AsyncClient, proj_auth_headers: dict
):
    """
    GET /api/projects/{id} for a project UUID that does not exist in the DB
    must return HTTP 404 with a detail message.
    """
    import uuid

    # Act
    response = await client.get(
        f"/api/projects/{uuid.uuid4()}", headers=proj_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_delete_nonexistent_project_returns_404(
    client: AsyncClient, proj_auth_headers: dict
):
    """
    DELETE /api/projects/{id} for a non-existent project must return HTTP 404.
    """
    import uuid

    # Act
    response = await client.delete(
        f"/api/projects/{uuid.uuid4()}", headers=proj_auth_headers
    )

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_project_returns_204(
    client: AsyncClient,
    proj_user: User,
    proj_auth_headers: dict,
):
    """
    DELETE /api/projects/{id} for an existing project must return HTTP 204
    No Content, and the project must no longer be retrievable.
    """
    # Arrange — create project
    payload = {"name": "to-delete", "owner_id": str(proj_user.id)}
    create_resp = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )
    project_id = create_resp.json()["id"]

    # Act — delete it
    delete_resp = await client.delete(
        f"/api/projects/{project_id}", headers=proj_auth_headers
    )

    # Assert — 204 on delete
    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT

    # Assert — follow-up GET returns 404
    get_resp = await client.get(
        f"/api/projects/{project_id}", headers=proj_auth_headers
    )
    assert get_resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_create_duplicate_project_returns_409(
    client: AsyncClient,
    proj_user: User,
    proj_auth_headers: dict,
):
    """
    Creating two projects with the same owner_id and name must trigger the
    UNIQUE constraint (uq_owner_project_name) and return HTTP 409 Conflict.
    """
    # Arrange — create first project
    payload = {"name": "duplicate-name", "owner_id": str(proj_user.id)}
    first_resp = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )
    assert first_resp.status_code == 201

    # Act — attempt to create an identical project
    second_resp = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )

    # Assert
    assert second_resp.status_code == status.HTTP_409_CONFLICT


# ---------------------------------------------------------------------------
# Tests — create_project without owner_id (auto-admin branch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_no_owner_id_admin_does_not_exist(
    client: AsyncClient,
    proj_auth_headers: dict,
):
    """
    When owner_id is omitted AND no 'admin' user exists, the endpoint must
    auto-create one and use it as the project owner — returns HTTP 201.

    This covers lines 40-51 (both the admin-not-found sub-branch).
    """
    # The test DB is clean per-test, so no 'admin' user exists yet.
    payload = {"name": "auto-admin-project-new"}

    response = await client.post(
        "/api/projects/", json=payload, headers=proj_auth_headers
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "auto-admin-project-new"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_project_no_owner_id_admin_already_exists(
    client: AsyncClient,
    proj_auth_headers: dict,
):
    """
    When owner_id is omitted AND an 'admin' user already exists, the endpoint
    must reuse that admin as the project owner — returns HTTP 201.

    This covers lines 40-51 (the admin-found sub-branch).
    """
    # First call auto-creates the admin user
    first = await client.post(
        "/api/projects/",
        json={"name": "admin-exists-proj-1"},
        headers=proj_auth_headers,
    )
    assert first.status_code == status.HTTP_201_CREATED

    # Second call: admin user now exists → reuse it
    second = await client.post(
        "/api/projects/",
        json={"name": "admin-exists-proj-2"},
        headers=proj_auth_headers,
    )
    assert second.status_code == status.HTTP_201_CREATED
    assert second.json()["name"] == "admin-exists-proj-2"


@pytest.mark.asyncio
async def test_create_project_generic_exception_returns_500(
    client: AsyncClient,
    proj_user: User,
    proj_auth_headers: dict,
):
    """
    When crud_project.create_project raises a generic (non-unique) exception,
    the endpoint must return HTTP 500 — covers the 'raise HTTPException(500)' branch.
    """
    from unittest.mock import AsyncMock, patch

    with patch(
        "app.api.endpoints.projects.crud_project.create_project",
        new=AsyncMock(side_effect=RuntimeError("unexpected db failure")),
    ):
        response = await client.post(
            "/api/projects/",
            json={"name": "boom", "owner_id": str(proj_user.id)},
            headers=proj_auth_headers,
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
