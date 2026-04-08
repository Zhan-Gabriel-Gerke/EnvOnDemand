"""
tests/crud/test_user.py
=======================
Tests for app.crud.user — user creation, password hashing,
default role assignment, and automatic UserQuota provisioning.

All tests use the real PostgreSQL test database via the `db_session` fixture
defined in tests/conftest.py. No mocking of the database layer is done.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.security import verify_password
from app.crud.user import create_user, get_user_by_username
from app.models.models import Role, User, UserQuota
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _make_user(
    db: AsyncSession,
    *,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "securepassword",
) -> User:
    """Convenience wrapper so tests can supply minimal keyword args."""
    user_in = UserCreate(username=username, email=email, password=password)
    return await create_user(db, user_in=user_in)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_persists_to_db(db_session: AsyncSession):
    """
    After create_user() the new User row must exist in the database with the
    correct username and email, and the returned object must not be None.
    """
    # Arrange
    user_in = UserCreate(
        username="alice",
        email="alice@example.com",
        password="alicepassword",
    )

    # Act
    user = await create_user(db_session, user_in=user_in)

    # Assert — returned object
    assert user is not None
    assert user.id is not None

    # Assert — round-trip to DB
    result = await db_session.execute(select(User).filter(User.username == "alice"))
    db_user = result.scalars().first()
    assert db_user is not None
    assert db_user.username == "alice"
    assert db_user.email == "alice@example.com"


@pytest.mark.asyncio
async def test_create_user_password_is_hashed(db_session: AsyncSession):
    """
    The plaintext password must never be stored in the database.
    hashed_password must be a valid bcrypt hash that verify_password() accepts.
    """
    # Arrange
    plain_password = "my_super_secret"
    user_in = UserCreate(
        username="bob",
        email="bob@example.com",
        password=plain_password,
    )

    # Act
    user = await create_user(db_session, user_in=user_in)

    # Assert — plaintext not stored
    assert user.hashed_password != plain_password
    assert plain_password not in user.hashed_password

    # Assert — hash is valid and verify_password works
    assert verify_password(plain_password, user.hashed_password) is True

    # Assert — wrong password must NOT verify
    assert verify_password("wrong_password", user.hashed_password) is False


@pytest.mark.asyncio
async def test_create_user_assigns_developer_role(db_session: AsyncSession):
    """
    create_user() must automatically bind the 'developer' role.
    If the role does not exist yet it must be created on the fly.
    """
    # Act
    user = await _make_user(db_session, username="carol", email="carol@e.com")

    # Assert — role relationship is not None (loaded via selectin)
    assert user.role is not None
    assert user.role.name == "developer"

    # Assert — the role row is actually in the DB
    result = await db_session.execute(
        select(Role).filter(Role.name == "developer")
    )
    dev_role = result.scalars().first()
    assert dev_role is not None
    assert dev_role.id == user.role_id


@pytest.mark.asyncio
async def test_create_user_role_reused_on_second_creation(db_session: AsyncSession):
    """
    Calling create_user() twice must not create a second 'developer' Role row —
    both users must share the same role (UNIQUE constraint on Role.name).
    """
    # Arrange — first user implicitly seeds the 'developer' role
    user_a = await _make_user(db_session, username="dave", email="dave@e.com")

    # Act — second user must reuse the existing role
    user_b = await _make_user(db_session, username="eve", email="eve@e.com")

    # Assert — same role id
    assert user_a.role_id == user_b.role_id

    # Assert — only one developer Role row exists
    result = await db_session.execute(
        select(Role).filter(Role.name == "developer")
    )
    roles = result.scalars().all()
    assert len(roles) == 1


@pytest.mark.asyncio
async def test_create_user_provisions_default_quota(db_session: AsyncSession):
    """
    A UserQuota row must be automatically created when a new user is registered.
    Default limits are max_containers=3 and active_containers=0.
    """
    # Act
    user = await _make_user(db_session, username="frank", email="frank@e.com")

    # Assert — via relationship (loaded via selectin)
    assert user.quota is not None
    assert user.quota.max_containers == 3
    assert user.quota.active_containers == 0
    assert user.quota.user_id == user.id

    # Assert — raw DB query
    result = await db_session.execute(
        select(UserQuota).filter(UserQuota.user_id == user.id)
    )
    quota = result.scalars().first()
    assert quota is not None
    assert quota.max_containers == 3
    assert quota.active_containers == 0


@pytest.mark.asyncio
async def test_create_user_quota_is_unique_per_user(db_session: AsyncSession):
    """
    Each user must have exactly one quota row (UNIQUE constraint on user_id).
    """
    # Act
    user = await _make_user(db_session, username="grace", email="grace@e.com")

    # Assert — only one quota row for this user
    result = await db_session.execute(
        select(UserQuota).filter(UserQuota.user_id == user.id)
    )
    quotas = result.scalars().all()
    assert len(quotas) == 1


@pytest.mark.asyncio
async def test_get_user_by_username_returns_existing_user(db_session: AsyncSession):
    """
    get_user_by_username() must return the correct User for a registered username.
    """
    # Arrange
    await _make_user(db_session, username="harry", email="harry@e.com")

    # Act
    fetched = await get_user_by_username(db_session, username="harry")

    # Assert
    assert fetched is not None
    assert fetched.username == "harry"
    assert fetched.email == "harry@e.com"


@pytest.mark.asyncio
async def test_get_user_by_username_returns_none_for_unknown(db_session: AsyncSession):
    """
    get_user_by_username() must return None if the username does not exist.
    """
    # Act
    result = await get_user_by_username(db_session, username="does_not_exist")

    # Assert
    assert result is None
