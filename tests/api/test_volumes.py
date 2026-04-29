"""
tests/api/test_volumes.py
Coverage tests for app.api.endpoints.volumes (36% → 100%)
Covers: list, create (success / duplicate / docker error), delete (404 / 403 / docker error / success).
"""
import uuid
import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.security import create_access_token
from app.crud.user import create_user
from app.models.models import User
from app.schemas.user import UserCreate
from app.services.docker_service import DockerServiceError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def vol_owner(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="vol_owner", email="vol_owner@test.com", password="pw"),
    )


@pytest_asyncio.fixture
async def vol_other(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="vol_other", email="vol_other@test.com", password="pw"),
    )


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def _docker_ctx_mock(raise_error: Exception | None = None):
    """Returns a mock async context manager for DockerService."""
    ds = MagicMock()
    if raise_error:
        ds.create_volume = AsyncMock(side_effect=raise_error)
        ds.remove_volume = AsyncMock(side_effect=raise_error)
    else:
        ds.create_volume = AsyncMock()
        ds.remove_volume = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=ds)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# GET /api/volumes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_volumes_empty(client: AsyncClient, vol_owner: User):
    resp = await client.get("/api/volumes", headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_volumes_returns_own(client: AsyncClient, vol_owner: User):
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        await client.post("/api/volumes", json={"name": "myvol1"}, headers=_auth(vol_owner))

    resp = await client.get("/api/volumes", headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_200_OK
    names = [v["name"] for v in resp.json()]
    assert "myvol1" in names


# ---------------------------------------------------------------------------
# POST /api/volumes — create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_volume_success_201(client: AsyncClient, vol_owner: User):
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        resp = await client.post("/api/volumes", json={"name": "newvol"}, headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["name"] == "newvol"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_volume_duplicate_name_400(client: AsyncClient, vol_owner: User):
    """Second POST with the same name → 400 (volume already exists in DB)."""
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        await client.post("/api/volumes", json={"name": "dupvol"}, headers=_auth(vol_owner))
        resp = await client.post("/api/volumes", json={"name": "dupvol"}, headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_create_volume_docker_error_400(client: AsyncClient, vol_owner: User):
    """DockerService.create_volume raises DockerServiceError → 400."""
    err = DockerServiceError("docker daemon unreachable")
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock(raise_error=err)):
        resp = await client.post("/api/volumes", json={"name": "failedvol"}, headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# DELETE /api/volumes/{volume_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_volume_not_found_404(client: AsyncClient, vol_owner: User):
    resp = await client.delete(f"/api/volumes/{uuid.uuid4()}", headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_volume_other_user_403(client: AsyncClient, vol_owner: User, vol_other: User):
    """vol_other tries to delete a volume owned by vol_owner → 403."""
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        create_resp = await client.post(
            "/api/volumes", json={"name": "othervol"}, headers=_auth(vol_owner)
        )
    vol_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/volumes/{vol_id}", headers=_auth(vol_other))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_delete_volume_success_204(client: AsyncClient, vol_owner: User):
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        create_resp = await client.post(
            "/api/volumes", json={"name": "delvol"}, headers=_auth(vol_owner)
        )
    vol_id = create_resp.json()["id"]

    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        resp = await client.delete(f"/api/volumes/{vol_id}", headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
async def test_delete_volume_docker_error_400(client: AsyncClient, vol_owner: User):
    """DockerService.remove_volume raises DockerServiceError → 400."""
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock()):
        create_resp = await client.post(
            "/api/volumes", json={"name": "errordelvol"}, headers=_auth(vol_owner)
        )
    vol_id = create_resp.json()["id"]

    err = DockerServiceError("volume in use")
    with patch("app.api.endpoints.volumes.DockerService", return_value=_docker_ctx_mock(raise_error=err)):
        resp = await client.delete(f"/api/volumes/{vol_id}", headers=_auth(vol_owner))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
