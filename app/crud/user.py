from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.models import User, Role, UserQuota
from app.schemas.user import UserCreate


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get a user by username."""
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalars().first()


from app.core.security import get_password_hash

async def create_user(db: AsyncSession, *, user_in: UserCreate) -> User:
    """
    Provisions a new user identity in the system.
    Automatically binds the baseline 'developer' role and initializes a standard resource quota.
    """
    # Resolve or initialize the baseline Role
    role_result = await db.execute(select(Role).filter(Role.name == "developer"))
    dev_role = role_result.scalars().first()
    if not dev_role:
        dev_role = Role(name="developer")
        db.add(dev_role)
        await db.commit()
        await db.refresh(dev_role)
    
    db_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        role_id=dev_role.id
    )
    db.add(db_user)
    
    # Pre-allocate standard limits upon account creation
    db_quota = UserQuota(
        user=db_user,
        max_containers=3,
        active_containers=0
    )
    db.add(db_quota)
    
    await db.commit()
    await db.refresh(db_user)
    return db_user
