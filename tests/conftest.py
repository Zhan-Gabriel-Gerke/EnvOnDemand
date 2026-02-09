import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncSession:
    """
    Pytest fixture to provide a database session for a single test function.
    It also handles cleanup by truncating all tables after the test runs.
    """
    test_engine = create_async_engine(settings.DATABASE_URL, echo=False)
    TestAsyncSessionLocal = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # Create all tables (in case the DB is empty)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = TestAsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()
        # Clean up data after the test for isolation
        async with test_engine.begin() as conn:
            # TRUNCATE cleans tables. RESTART IDENTITY resets sequences.
            # CASCADE deletes dependent records in other tables.
            table_names = [table.name for table in reversed(Base.metadata.sorted_tables)]
            if table_names:
                await conn.execute(
                    f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"
                )
    await test_engine.dispose()
