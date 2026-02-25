import bcrypt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a hashed one using native bcrypt."""
    try:
        # bcrypt requires bytes
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        return False

def get_password_hash(password: str) -> str:
    """Returns the bcrypt hash of a password as a string."""
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_bytes.decode('utf-8')
