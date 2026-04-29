"""
tests/crud/test_blueprint.py
Coverage tests for app.crud.blueprint — all functions.
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import blueprint as crud_blueprint
from app.schemas.blueprint import BlueprintCreate, BlueprintUpdate


@pytest.mark.asyncio
async def test_get_blueprint_not_found(db_session: AsyncSession):
    result = await crud_blueprint.get_blueprint(db_session, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_blueprints_pagination(db_session: AsyncSession):
    result = await crud_blueprint.get_blueprints(db_session, skip=0, limit=5)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_create_blueprint(db_session: AsyncSession):
    bp = await crud_blueprint.create_blueprint(
        db_session,
        blueprint_in=BlueprintCreate(
            name=f"bp-{uuid.uuid4().hex[:6]}",
            image_tag="nginx:latest",
            default_port=80,
        ),
    )
    assert bp.id is not None
    # verify it's fetchable
    found = await crud_blueprint.get_blueprint(db_session, bp.id)
    assert found.name == bp.name


@pytest.mark.asyncio
async def test_update_blueprint(db_session: AsyncSession):
    bp = await crud_blueprint.create_blueprint(
        db_session,
        blueprint_in=BlueprintCreate(
            name=f"updbp-{uuid.uuid4().hex[:6]}",
            image_tag="nginx:latest",
            default_port=80,
        ),
    )
    updated = await crud_blueprint.update_blueprint(
        db_session,
        db_obj=bp,
        obj_in=BlueprintUpdate(image_tag="nginx:alpine"),
    )
    assert updated.image_tag == "nginx:alpine"
