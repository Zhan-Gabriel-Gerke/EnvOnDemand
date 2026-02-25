import uuid
from typing import List

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.session import get_db
from app.models.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Resolves the active user identity from the provided token.
    Automatically fetches Role and Quota due to "selectin" strategy in SQLAlchemy configs.
    
    TODO: Integrate PyJWT to decode structurally sound RS256/HS256 tokens and validate 
    exp/nbf claims. Currently assuming token == user.id for local test isolation.
    """
    try:
        user_id = uuid.UUID(token) 
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Session linked to a defunct user identity"
        )
        
    return user


class RequireRole:
    """
    Dependency factory leveraging FastAPI's injection system to enforce RBAC cleanly 
    at the router edge. Rejects unauthorized attempts with minimal DB overhead.
    """
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    async def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if not current_user.role or current_user.role.name not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires one of the following capabilities: {', '.join(self.allowed_roles)}"
            )
        return current_user
