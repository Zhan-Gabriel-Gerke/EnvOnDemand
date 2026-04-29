"""
tests/test_security.py
Coverage tests for app.core.security
Covers every function and every branch (try/except + if/else).
"""
import pytest
from datetime import timedelta
from unittest.mock import patch

import bcrypt
import jwt

from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token,
    ALGORITHM,
)
from app.core.config import settings


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------

def test_verify_password_correct():
    """Happy path: matching password returns True."""
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("utf-8")
    assert verify_password("secret", hashed) is True


def test_verify_password_wrong():
    """Wrong password returns False (via bcrypt.checkpw)."""
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("utf-8")
    assert verify_password("wrong", hashed) is False


def test_verify_password_value_error_branch():
    """bcrypt.checkpw raises ValueError → except branch returns False."""
    with patch("app.core.security.bcrypt.checkpw", side_effect=ValueError("bad hash")):
        result = verify_password("any", "not-a-hash")
    assert result is False


# ---------------------------------------------------------------------------
# get_password_hash
# ---------------------------------------------------------------------------

def test_get_password_hash_returns_string():
    """Returns a non-empty bcrypt hash string."""
    h = get_password_hash("mypassword")
    assert isinstance(h, str)
    assert h.startswith("$2b$")


def test_get_password_hash_is_verifiable():
    """The hash produced can be verified with bcrypt directly."""
    password = "testpass123"
    h = get_password_hash(password)
    assert bcrypt.checkpw(password.encode("utf-8"), h.encode("utf-8"))


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------

def test_create_access_token_default_expiry():
    """Without expires_delta, uses ACCESS_TOKEN_EXPIRE_MINUTES — default branch."""
    token = create_access_token(data={"sub": "user-123"})
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "user-123"
    assert "exp" in payload


def test_create_access_token_custom_expiry():
    """With an explicit expires_delta, the `if expires_delta` branch is taken."""
    token = create_access_token(
        data={"sub": "user-456"},
        expires_delta=timedelta(minutes=5),
    )
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "user-456"
    assert "exp" in payload


# ---------------------------------------------------------------------------
# decode_access_token
# ---------------------------------------------------------------------------

def test_decode_access_token_valid():
    """Decoding a freshly created token returns the correct payload."""
    token = create_access_token(data={"sub": "decode-me"})
    payload = decode_access_token(token)
    assert payload["sub"] == "decode-me"


def test_decode_access_token_invalid_raises():
    """An invalid token must raise jwt.exceptions.InvalidTokenError."""
    with pytest.raises(jwt.exceptions.InvalidTokenError):
        decode_access_token("totally.invalid.token")


def test_decode_access_token_expired_raises():
    """An already-expired token must raise an InvalidTokenError subclass."""
    expired_token = create_access_token(
        data={"sub": "gone"}, expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(jwt.exceptions.InvalidTokenError):
        decode_access_token(expired_token)
