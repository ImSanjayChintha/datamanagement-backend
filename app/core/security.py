"""
Module: core.security
Purpose: JWT token creation/verification, password hashing, API key generation,
         and marketplace webhook signature verification.

All cryptographic operations are centralised here to make key rotation,
algorithm changes, and audit logging straightforward.
"""
import secrets
import hmac
import hashlib
import time
from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import settings
from app.core.constants import WEBHOOK_MAX_AGE_SECONDS

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    """Hash a plain-text password using bcrypt.

    Args:
        password: Plain-text password string.

    Returns:
        bcrypt hash string suitable for storing in the database.
    """
    return pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash.

    Args:
        plain: Plain-text password to check.
        hashed: Stored bcrypt hash from the database.

    Returns:
        True if the password matches, False otherwise.
    """
    return pwd_ctx.verify(plain, hashed)


def create_access_token(subject: str, role: str, extra: dict | None = None) -> str:
    """Create a short-lived JWT access token.

    Args:
        subject: Token subject (typically the user id as a string).
        role: User role string embedded in the payload.
        extra: Optional additional claims to merge into the payload.

    Returns:
        Encoded JWT string.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "role": role, "exp": expire, **(extra or {})}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        subject: Token subject (typically the user id as a string).

    Returns:
        Encoded JWT string with type='refresh' claim.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": subject, "type": "refresh", "exp": expire},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token.

    Args:
        token: Encoded JWT string.

    Returns:
        Decoded payload dict.

    Raises:
        jose.JWTError: If the token is invalid, expired, or has a bad signature.
    """
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_api_key() -> tuple[str, str]:
    """Generate a new marketplace API key pair.

    Returns:
        Tuple of (key_id, raw_secret). Store only the hash of raw_secret
        in the database using hash_secret().
    """
    key_id     = secrets.token_urlsafe(16)
    raw_secret = secrets.token_urlsafe(32)
    return key_id, raw_secret


def hash_secret(raw: str) -> str:
    """Hash a raw API secret using bcrypt.

    Args:
        raw: Raw secret string to hash.

    Returns:
        bcrypt hash string.
    """
    return pwd_ctx.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    """Verify a raw API secret against its stored bcrypt hash.

    Args:
        raw: Raw secret string to verify.
        hashed: Stored bcrypt hash from the database.

    Returns:
        True if the secret matches, False otherwise.
    """
    return pwd_ctx.verify(raw, hashed)


def verify_marketplace_signature(
    payload: bytes,
    signature: str,
    secret: str,
    timestamp: str,
    max_age_secs: int = WEBHOOK_MAX_AGE_SECONDS,
) -> bool:
    """Verify an HMAC-SHA256 webhook signature from a marketplace integration.

    The expected signature is computed as:
        HMAC-SHA256("{timestamp}.{payload_bytes}", secret)

    Args:
        payload: Raw request body bytes.
        signature: Hex-encoded signature from the webhook header.
        secret: Shared secret for the marketplace integration.
        timestamp: Unix timestamp string from the webhook header.
        max_age_secs: Maximum allowed age of the webhook (default 300 s).

    Returns:
        True if the signature is valid and the timestamp is within max_age_secs.
    """
    try:
        ts = int(timestamp)
        if abs(time.time() - ts) > max_age_secs:
            return False
        msg      = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False
