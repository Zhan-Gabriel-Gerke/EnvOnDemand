"""
tests/crud/test_crud_volume.py
Coverage tests for app.crud.volume — all functions and branches.
"""
import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.volume import (
    get_volume,
    get_volume_by_name,
    get_user_volumes,
    create_volume,
    delete_volume,
)
from app.crud.user import create_user
from app.models.models import User
from app.schemas.user import UserCreate
from app.schemas.volume import VolumeCreate


@pytest_asyncio.fixture
async def vol_user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="crud_vol_user", email="crud_vol@test.com", password="pw"),
    )


# ---------------------------------------------------------------------------
# get_volume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_volume_returns_none_when_missing(db_session: AsyncSession):
    result = await get_volume(db_session, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_volume_returns_volume(db_session: AsyncSession, vol_user: User):
    vol = await create_volume(db_session, VolumeCreate(name="testvol1"), vol_user.id)
    found = await get_volume(db_session, vol.id)
    assert found is not None
    assert found.id == vol.id


# ---------------------------------------------------------------------------
# get_volume_by_name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_volume_by_name_returns_none(db_session: AsyncSession):
    result = await get_volume_by_name(db_session, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_volume_by_name_returns_volume(db_session: AsyncSession, vol_user: User):
    await create_volume(db_session, VolumeCreate(name="namedvol"), vol_user.id)
    found = await get_volume_by_name(db_session, "namedvol")
    assert found is not None
    assert found.name == "namedvol"


# ---------------------------------------------------------------------------
# get_user_volumes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_user_volumes_empty(db_session: AsyncSession, vol_user: User):
    result = await get_user_volumes(db_session, vol_user.id)
    assert result == []


@pytest.mark.asyncio
async def test_get_user_volumes_returns_own(db_session: AsyncSession, vol_user: User):
    await create_volume(db_session, VolumeCreate(name="uservol1"), vol_user.id)
    await create_volume(db_session, VolumeCreate(name="uservol2"), vol_user.id)
    result = await get_user_volumes(db_session, vol_user.id)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# create_volume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_volume_returns_volume_with_id(db_session: AsyncSession, vol_user: User):
    vol = await create_volume(db_session, VolumeCreate(name="created_vol"), vol_user.id)
    assert vol.id is not None
    assert vol.name == "created_vol"
    assert vol.user_id == vol_user.id


# ---------------------------------------------------------------------------
# delete_volume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_volume_existing(db_session: AsyncSession, vol_user: User):
    vol = await create_volume(db_session, VolumeCreate(name="delvol"), vol_user.id)
    await delete_volume(db_session, vol.id)
    result = await get_volume(db_session, vol.id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_volume_nonexistent_does_not_raise(db_session: AsyncSession):
    """delete_volume on a missing UUID must silently do nothing (if db_volume branch)."""
    await delete_volume(db_session, uuid.uuid4())  # no exception expected
