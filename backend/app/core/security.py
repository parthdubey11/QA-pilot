"""Password hashing (bcrypt), JWT access/refresh tokens and at-rest encryption for test-site credentials."""

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]
JWT_ALGORITHM = "HS256"
BCRYPT_MAX_BYTES = 72  # bcrypt ignores (newer versions reject) anything longer


class InvalidTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Used to spend the same bcrypt time when the email doesn't exist, so login timing doesn't reveal accounts.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def create_token(user_id: str, token_type: TokenType, ttl: timedelta | None = None) -> str:
    settings = get_settings()
    if ttl is None:
        ttl = (timedelta(minutes=settings.access_token_minutes) if token_type == "access"
               else timedelta(days=settings.refresh_token_days))
    now = datetime.now(UTC)
    claims = {"sub": user_id, "type": token_type, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_type: TokenType) -> str:
    """Return the user id in a valid token of the expected type, else raise InvalidTokenError."""
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM],
                            options={"require": ["sub", "type", "exp"]})
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if claims["type"] != expected_type:
        raise InvalidTokenError(f"expected a {expected_type} token")
    return str(claims["sub"])


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.credentials_key or base64.urlsafe_b64encode(
        hashlib.sha256(b"qa-pilot-credentials:" + settings.jwt_secret.encode()).digest()
    ).decode()
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
