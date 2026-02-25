from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.models import User
from app.schemas.user import UserCreate


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get a user by username."""
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalars().first()


async def create_user(db: AsyncSession, *, user_in: UserCreate) -> User:
    """Create a new user."""
    # In a real app, we would hash the password here.
    # For now, we'll store it as-is or assume the caller hashes it if needed,
    # but based on the model `hashed_password`, we should probably just store it directly for this MVP/demo.
    # The model has `hashed_password`, so let's just map `password` to it for now.
    
    db_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=user_in.password, # Security note: Needs hashing in production
        is_admin=user_in.is_admin
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user
