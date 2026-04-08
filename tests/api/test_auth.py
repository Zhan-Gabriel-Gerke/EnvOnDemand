"""
tests/api/test_auth.py
======================
Integration tests for the POST /api/auth/token endpoint.

Covers:
  - Successful login returns a signed JWT (HTTP 200, bearer token).
  - Failed login with wrong password returns HTTP 401.
  - Failed login with unknown username returns HTTP 401.
  - WWW-Authenticate header is present on failure responses.

All tests use the real test database (via `db_session` + `client` from conftest).
"""
import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import jwt

from app.core.config import settings
from app.core.security import ALGORITHM
from app.crud.user import create_user
from app.models.models import User
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# Module-level fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def auth_user(db_session: AsyncSession) -> User:
    """
    Register a fresh user that only exists for auth tests.
    Isolated from the shared conftest test_user to keep these tests self-contained.
    """
    user_in = UserCreate(
        username="authuser",
        email="auth@test.com",
        password="authpassword",
    )
    return await create_user(db_session, user_in=user_in)


# ---------------------------------------------------------------------------
# Tests — successful login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_200_and_bearer_token(
    auth_user: User, client: AsyncClient
):
    """
    A POST to /api/auth/token with valid credentials must return HTTP 200,
    a JSON body with `access_token` and `token_type` = 'bearer'.
    """
    # Arrange
    form_data = {"username": "authuser", "password": "authpassword"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert — status code and structure
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["access_token"]  # non-empty string


@pytest.mark.asyncio
async def test_login_jwt_contains_correct_sub_claim(
    auth_user: User, client: AsyncClient
):
    """
    The JWT returned by a successful login must encode the user's UUID
    in the `sub` claim (HS256 signed with the application SECRET_KEY).
    """
    # Arrange
    form_data = {"username": "authuser", "password": "authpassword"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert — decode and inspect claims
    token = response.json()["access_token"]
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    assert payload.get("sub") == str(auth_user.id)


@pytest.mark.asyncio
async def test_login_jwt_has_exp_claim(auth_user: User, client: AsyncClient):
    """
    The JWT must contain an `exp` (expiry) claim — absence would allow
    tokens to be valid forever.
    """
    # Arrange
    form_data = {"username": "authuser", "password": "authpassword"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert
    token = response.json()["access_token"]
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    assert "exp" in payload


# ---------------------------------------------------------------------------
# Tests — failed login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(
    auth_user: User, client: AsyncClient
):
    """
    Supplying a wrong password for an existing user must return HTTP 401
    and a JSON error body with a `detail` key.
    """
    # Arrange
    form_data = {"username": "authuser", "password": "WRONG_PASSWORD"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_login_wrong_password_includes_www_authenticate_header(
    auth_user: User, client: AsyncClient
):
    """
    Per RFC 7235, the response to an authentication failure must include
    a `WWW-Authenticate` header.
    """
    # Arrange
    form_data = {"username": "authuser", "password": "WRONG_PASSWORD"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert
    assert "www-authenticate" in response.headers


@pytest.mark.asyncio
async def test_login_unknown_username_returns_401(client: AsyncClient):
    """
    Attempting to log in with a username that does not exist must return
    HTTP 401 — not 404 (to prevent username enumeration).
    """
    # Arrange
    form_data = {"username": "ghost_user_does_not_exist", "password": "whatever"}

    # Act
    response = await client.post("/api/auth/token", data=form_data)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_empty_credentials_returns_422(client: AsyncClient):
    """
    Completely omitting both username and password must trigger FastAPI's
    request validation and return HTTP 422 Unprocessable Entity.
    """
    # Act
    response = await client.post("/api/auth/token", data={})

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
