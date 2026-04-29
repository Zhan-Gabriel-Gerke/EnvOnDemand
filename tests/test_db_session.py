"""
tests/test_db_session.py
Tests for app.db.session — ensures the get_db generator body is executed.
"""
import pytest


@pytest.mark.asyncio
async def test_get_db_yields_async_session():
    """Covers lines 26-27: the `async with AsyncSessionLocal() as session: yield session` body."""
    from app.db.session import get_db
    gen = get_db()
    session = await gen.__anext__()
    assert session is not None
    try:
        await gen.aclose()
    except StopAsyncIteration:
        pass
