from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Create an asynchronous SQLAlchemy engine using the database URL from settings.
# `echo=True` enables logging of all SQL statements.
engine = create_async_engine(settings.DATABASE_URL, echo=True)

# Configure an asynchronous sessionmaker for creating AsyncSession objects.
# `expire_on_commit=False` prevents objects from being expired after commit,
# allowing them to be used outside the session context if needed.
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False, # Disable autocommit for explicit transaction control.
    autoflush=False, # Disable autoflush for explicit flush control.
)

async def get_db():
    """
    Dependency function to provide an asynchronous database session.
    This function can be used with FastAPI's Depends to inject a session
    into route handlers. The session is automatically closed after the request.
    """
    async with AsyncSessionLocal() as session:
        yield session
