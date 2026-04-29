"""
tests/api/test_blueprints.py
Coverage tests for app.api.endpoints.blueprints
Covers: list, create, read (found / not found), update (found / not found).
"""
import uuid
import pytest
from httpx import AsyncClient
from fastapi import status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BLUEPRINT_PAYLOAD = {
    "name": "nginx-blueprint",
    "image_tag": "nginx:latest",
    "default_port": 80,
    "default_env_vars": {},
    "cpu_limit": None,
    "mem_limit": None,
}


async def _create_blueprint(client: AsyncClient) -> dict:
    resp = await client.post("/api/blueprints/", json=BLUEPRINT_PAYLOAD)
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# list_blueprints  (GET /)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_blueprints_returns_200(authenticated_client: AsyncClient):
    resp = await authenticated_client.get("/api/blueprints/")
    assert resp.status_code == status.HTTP_200_OK
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# create_blueprint  (POST /)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_blueprint_returns_201(authenticated_client: AsyncClient):
    resp = await authenticated_client.post("/api/blueprints/", json=BLUEPRINT_PAYLOAD)
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["name"] == BLUEPRINT_PAYLOAD["name"]
    assert "id" in data


# ---------------------------------------------------------------------------
# read_blueprint  (GET /{blueprint_id})
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_blueprint_found_returns_200(authenticated_client: AsyncClient):
    created = await _create_blueprint(authenticated_client)
    resp = await authenticated_client.get(f"/api/blueprints/{created['id']}")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_read_blueprint_not_found_returns_404(authenticated_client: AsyncClient):
    missing_id = str(uuid.uuid4())
    resp = await authenticated_client.get(f"/api/blueprints/{missing_id}")
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# update_blueprint  (PATCH /{blueprint_id})
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_blueprint_found_returns_200(authenticated_client: AsyncClient):
    created = await _create_blueprint(authenticated_client)
    patch_payload = {"name": "updated-blueprint"}
    resp = await authenticated_client.patch(f"/api/blueprints/{created['id']}", json=patch_payload)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["name"] == "updated-blueprint"


@pytest.mark.asyncio
async def test_update_blueprint_not_found_returns_404(authenticated_client: AsyncClient):
    missing_id = str(uuid.uuid4())
    resp = await authenticated_client.patch(f"/api/blueprints/{missing_id}", json={"name": "x"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND
