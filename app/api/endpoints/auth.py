from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.session import get_db
from app.models.models import User
from app.core.security import verify_password, create_access_token

router = APIRouter()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate a user and return a signed JWT access token (HS256).

    - **username**: registered username
    - **password**: plain-text password (compared against bcrypt hash)

    Returns a bearer token valid for ``ACCESS_TOKEN_EXPIRE_MINUTES`` minutes.
    """
    result = await db.execute(select(User).filter(User.username == form_data.username))
    user = result.scalars().first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Encode the user's UUID as the `sub` claim — this is the standard way
    # to bind a token to a specific identity without leaking internal details.
    access_token = create_access_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=access_token)
