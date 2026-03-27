from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import settings

ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    """Returns the bcrypt hash of a password as a UTF-8 string."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# ---------------------------------------------------------------------------
# JWT utilities
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Creates a signed JWT access token.

    Args:
        data:          The payload to encode (typically contains ``sub``).
        expires_delta: Custom TTL. Defaults to ``ACCESS_TOKEN_EXPIRE_MINUTES``.

    Returns:
        A signed JWT string (HS256).
    """
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decodes and validates a JWT access token.

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        The decoded payload dict (contains ``sub``, ``exp``, …).

    Raises:
        jwt.exceptions.InvalidTokenError: If the token is expired, tampered
            with, or otherwise invalid. Callers must handle this and return 401.
    """
    # Will raise InvalidTokenError (or its subclasses ExpiredSignatureError,
    # DecodeError, etc.) if validation fails.
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
