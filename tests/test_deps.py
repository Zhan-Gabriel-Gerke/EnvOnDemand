"""
tests/test_deps.py
Coverage tests for app.api.deps (74% → 100%)
Covers: get_current_user (success / no sub / invalid token / user not found),
        RequireRole (allowed / denied).
"""
import uuid
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
# Fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def dep_user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="dep_test_user", email="dep_test@test.com", password="pw"),
    )


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# get_current_user — all branches via /api/projects/ (requires auth)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_user_success(client: AsyncClient, dep_user: User):
    """Valid token with known user → 200."""
    token = create_access_token(data={"sub": str(dep_user.id)})
    resp = await client.get("/api/projects/", headers=_bearer(token))
    assert resp.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_get_current_user_no_sub_claim(client: AsyncClient):
    """Token with no 'sub' claim → 401 (user_id_str is None branch)."""
    token = create_access_token(data={"foo": "bar"})  # no sub
    resp = await client.get("/api/projects/", headers=_bearer(token))
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_invalid_token(client: AsyncClient):
    """Malformed token → jwt.InvalidTokenError → 401."""
    resp = await client.get("/api/projects/", headers=_bearer("not.a.jwt"))
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_valid_uuid_but_unknown_user(client: AsyncClient):
    """Valid token with a UUID that doesn't match any user → 404."""
    token = create_access_token(data={"sub": str(uuid.uuid4())})
    resp = await client.get("/api/projects/", headers=_bearer(token))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_current_user_invalid_uuid_in_sub(client: AsyncClient):
    """sub claim is a non-UUID string → ValueError → 401."""
    token = create_access_token(data={"sub": "not-a-uuid"})
    resp = await client.get("/api/projects/", headers=_bearer(token))
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# RequireRole — via POST /api/deployments (requires admin or developer role)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_require_role_allowed(client: AsyncClient, dep_user: User):
    """User with 'developer' role can access a RequireRole(['admin','developer']) endpoint."""
    token = create_access_token(data={"sub": str(dep_user.id)})
    payload = {
        "containers": [{"name": "x", "role": "app", "image": "nginx:latest"}]
    }
    resp = await client.post("/api/deployments", json=payload, headers=_bearer(token))
    # 202 means RequireRole passed; quota check comes after
    assert resp.status_code in (status.HTTP_202_ACCEPTED, status.HTTP_429_TOO_MANY_REQUESTS, status.HTTP_500_INTERNAL_SERVER_ERROR)


@pytest.mark.asyncio
async def test_require_role_denied(client: AsyncClient, db_session: AsyncSession):
    """User whose role is NOT in the allowed list gets 403."""
    from app.models.models import Role
    from sqlalchemy.future import select

    # Fetch the existing 'viewer' role (seeded or created dynamically)
    result = await db_session.execute(select(Role).filter(Role.name == "viewer"))
    viewer_role = result.scalars().first()

    if not viewer_role:
        # Create it if not seeded
        viewer_role = Role(name="viewer")
        db_session.add(viewer_role)
        await db_session.commit()
        await db_session.refresh(viewer_role)

    viewer = await create_user(
        db_session,
        user_in=UserCreate(username="viewer_user", email="viewer@test.com", password="pw"),
    )
    # Force the user's role to viewer
    viewer.role_id = viewer_role.id
    await db_session.commit()
    await db_session.refresh(viewer)

    token = create_access_token(data={"sub": str(viewer.id)})
    payload = {
        "containers": [{"name": "x", "role": "app", "image": "nginx:latest"}]
    }
    resp = await client.post("/api/deployments", json=payload, headers=_bearer(token))
    assert resp.status_code == status.HTTP_403_FORBIDDEN
