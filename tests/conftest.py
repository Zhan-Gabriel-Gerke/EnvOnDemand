"""
tests/conftest.py
==================
Root conftest: async fixtures for all test modules.

Event-loop strategy
--------------------
asyncpg connections must be created inside the same event loop that runs the
tests. We therefore:

1. Set ``asyncio_mode = auto`` + ``asyncio_default_fixture_loop_scope = session``
   in ``pytest.ini`` so that **all** async fixtures and tests share a single,
   session-scoped event loop.

2. Create the SQLAlchemy engine inside a session-scoped async fixture
   (``_engine``) so the asyncpg pool is born inside that shared loop.

3. Override the app's ``get_db`` dependency so HTTP requests (via the
   ``client`` fixture) share the **same per-test session** as the test body.
   This eliminates concurrent-connection races between the fixture and the app.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, event

from app.core.config import settings
from app.core.security import create_access_token
from app.crud.user import create_user
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.schemas.user import UserCreate
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Session-scoped engine — created inside the shared event loop
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def _engine():
    """
    Create an async SQLAlchemy engine inside the session event loop.
    Ensures asyncpg pool connections are bound to the correct loop.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    # Create schema once for the entire session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def _session_factory(_engine):
    """Returns a sessionmaker bound to the session-scoped engine."""
    return sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# db_session fixture (function scope) — cleans up via TRUNCATE
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db_session(_session_factory, _engine) -> AsyncSession:
    """
    Provides a fresh AsyncSession for each test function.

    After the test completes, all tables are TRUNCATEd so each test starts
    with an empty database — eliminating ordering dependencies.
    """
    session: AsyncSession = _session_factory()
    try:
        yield session
    finally:
        await session.close()
        async with _engine.begin() as conn:
            table_names = [
                t.name for t in reversed(Base.metadata.sorted_tables)
            ]
            if table_names:
                await conn.execute(
                    text(
                        f"TRUNCATE TABLE {', '.join(table_names)} "
                        f"RESTART IDENTITY CASCADE"
                    )
                )


# ---------------------------------------------------------------------------
# HTTP client fixture — shares the same session with the app
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncClient:
    """
    HTTPX AsyncClient driving the FastAPI app in-process.

    The app's ``get_db`` dependency is overridden to yield the *same*
    ``db_session`` used by the test body.  This guarantees that:
    - Data committed by fixtures is visible to the API handler.
    - A single asyncpg connection is used, preventing "another operation
      is in progress" errors.
    """
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Shared user / auth fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def test_user(db_session: AsyncSession):
    """
    Creates a standard 'developer'-role user in the test DB.
    Available across all test modules via conftest.
    """
    user_in = UserCreate(
        username="fixture_user",
        email="fixture@test.com",
        password="fixture_password",
    )
    return await create_user(db_session, user_in=user_in)


@pytest_asyncio.fixture(scope="function")
def auth_headers(test_user) -> dict:
    """
    Authorization header dict for ``test_user``.
    Uses the correct ``data={"sub": ...}`` call signature.
    """
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def authenticated_client(test_user, db_session: AsyncSession) -> AsyncClient:
    """
    HTTPX client pre-configured with a valid JWT for ``test_user``.
    ``get_db`` is overridden the same way as in ``client``.
    """
    token = create_access_token(data={"sub": str(test_user.id)})

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
