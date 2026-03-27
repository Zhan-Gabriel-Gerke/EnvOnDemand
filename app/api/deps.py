import uuid
from typing import List

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolves the active user identity from a signed JWT bearer token.

    - Decodes and validates the token signature and expiry via PyJWT.
    - Extracts ``sub`` claim (user UUID) and fetches the User from the DB.

    Raises HTTP 401 on any token problem (expired, tampered, malformed).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = uuid.UUID(user_id_str)
    except (jwt.exceptions.InvalidTokenError, ValueError):
        # InvalidTokenError covers expired, tampered, decode errors.
        # ValueError covers malformed UUID in sub claim.
        raise credentials_exception

    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session linked to a defunct user identity",
        )

    return user


class RequireRole:
    """
    Dependency factory to enforce RBAC at the router edge.
    Rejects unauthorized attempts with a 403 before any business logic runs.
    """

    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    async def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if not current_user.role or current_user.role.name not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires one of the following roles: {', '.join(self.allowed_roles)}",
            )
        return current_user
