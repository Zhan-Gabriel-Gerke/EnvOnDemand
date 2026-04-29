"""
tests/crud/test_crud_project.py
Coverage tests for app.crud.project — all functions and branches.
"""
import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.project import (
    get_project,
    get_projects,
    create_project,
    delete_project,
)
from app.crud.user import create_user
from app.models.models import User
from app.schemas.user import UserCreate
from app.schemas.project import ProjectCreate


@pytest_asyncio.fixture
async def proj_owner(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        user_in=UserCreate(username="crud_proj_user", email="crud_proj@test.com", password="pw"),
    )


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_returns_none_when_missing(db_session: AsyncSession):
    result = await get_project(db_session, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_project_returns_project(db_session: AsyncSession, proj_owner: User):
    proj = await create_project(
        db_session,
        project_in=ProjectCreate(name="proj1"),
        owner_id=proj_owner.id,
    )
    found = await get_project(db_session, proj.id)
    assert found is not None
    assert found.id == proj.id


# ---------------------------------------------------------------------------
# get_projects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_projects_empty(db_session: AsyncSession):
    result = await get_projects(db_session)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_projects_returns_all(db_session: AsyncSession, proj_owner: User):
    await create_project(db_session, project_in=ProjectCreate(name="p1"), owner_id=proj_owner.id)
    await create_project(db_session, project_in=ProjectCreate(name="p2"), owner_id=proj_owner.id)
    result = await get_projects(db_session)
    names = [p.name for p in result]
    assert "p1" in names
    assert "p2" in names


@pytest.mark.asyncio
async def test_get_projects_skip_and_limit(db_session: AsyncSession, proj_owner: User):
    for i in range(3):
        await create_project(
            db_session,
            project_in=ProjectCreate(name=f"limit_proj_{i}"),
            owner_id=proj_owner.id,
        )
    result = await get_projects(db_session, skip=0, limit=2)
    assert len(result) <= 2


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_with_description(db_session: AsyncSession, proj_owner: User):
    proj = await create_project(
        db_session,
        project_in=ProjectCreate(name="withDesc", description="my desc"),
        owner_id=proj_owner.id,
    )
    assert proj.description == "my desc"
    assert proj.owner_id == proj_owner.id


# ---------------------------------------------------------------------------
# delete_project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_project_removes_it(db_session: AsyncSession, proj_owner: User):
    proj = await create_project(
        db_session,
        project_in=ProjectCreate(name="todelete"),
        owner_id=proj_owner.id,
    )
    await delete_project(db_session, proj.id)
    assert await get_project(db_session, proj.id) is None


@pytest.mark.asyncio
async def test_delete_project_nonexistent_does_not_raise(db_session: AsyncSession):
    """delete_project with missing UUID → if project: branch is False, silently exits."""
    await delete_project(db_session, uuid.uuid4())
