"""
Module: core.deps
Purpose: FastAPI dependency functions for authentication and authorization.

All dependencies validate JWT tokens and look up the corresponding user record.
Role checks use constants from core.constants to avoid hardcoded role strings.
"""
from fastapi import Header, Depends, HTTPException, status
from jose import JWTError
import asyncpg
from app.core.constants import ADMIN_ROLES, WRITER_ROLES, BEARER_SCHEME, MAX_QUERY_LIMIT, DEFAULT_QUERY_LIMIT
from app.core.database import get_db, row
from app.core.security import decode_token, verify_secret


async def get_current_admin(
    authorization: str = Header(...),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """FastAPI dependency: validate the admin Bearer token and return the admin record.

    Checks that the token is valid, the role is in ADMIN_ROLES, and the admin
    account is active in the database.

    Args:
        authorization: 'Authorization: Bearer <token>' header value.
        db: Pooled database connection (injected by FastAPI).

    Returns:
        Admin user dict from admin_users.

    Raises:
        HTTPException 401: If the header is malformed, the token is invalid,
            or the admin is not found / inactive.
        HTTPException 403: If the role is not in ADMIN_ROLES.
    """
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != BEARER_SCHEME:
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        payload = decode_token(token)
        role = payload.get("role", "")
        if role not in ADMIN_ROLES:
            raise HTTPException(status_code=403, detail="Admin access required")
        admin_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    admin = await row(db, "SELECT * FROM admin.v_admin_users WHERE id=$1 AND is_active=true", admin_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found or inactive")
    return admin


async def require_admin_role(admin: dict = Depends(get_current_admin)) -> dict:
    """FastAPI dependency: require admin or local_admin role.

    Depends on get_current_admin and additionally restricts to elevated roles.

    Args:
        admin: Admin dict from get_current_admin.

    Returns:
        Admin dict unchanged.

    Raises:
        HTTPException 403: If the role is not admin or local_admin.
    """
    if admin["role"] not in WRITER_ROLES:
        raise HTTPException(status_code=403, detail="Admin or Local Admin role required")
    return admin


async def get_current_customer(
    authorization: str = Header(...),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """FastAPI dependency: validate the customer Bearer token and return the customer record.

    Args:
        authorization: 'Authorization: Bearer <token>' header value.
        db: Pooled database connection.

    Returns:
        Customer dict from the customers table.

    Raises:
        HTTPException 401: If the token is invalid or the customer is not found.
        HTTPException 403: If the token role is not 'customer'.
    """
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != BEARER_SCHEME:
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        payload = decode_token(token)
        if payload.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Customer access required")
        customer_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    customer = await row(db, "SELECT * FROM customers WHERE id=$1", customer_id)
    if not customer:
        raise HTTPException(status_code=401, detail="Customer not found")
    return customer


async def get_optional_customer(
    authorization: str = Header(default=None),
    db: asyncpg.Connection = Depends(get_db),
) -> dict | None:
    """FastAPI dependency: return the customer if a valid token is provided, else None.

    Args:
        authorization: Optional 'Authorization: Bearer <token>' header value.
        db: Pooled database connection.

    Returns:
        Customer dict, or None if no valid token was provided.
    """
    if not authorization:
        return None
    try:
        return await get_current_customer(authorization=authorization, db=db)
    except HTTPException:
        return None


async def get_marketplace_key(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    x_api_secret: str = Header(..., alias="X-Api-Secret"),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """FastAPI dependency: validate marketplace API key and secret.

    Args:
        x_api_key: API key ID from the X-Api-Key header.
        x_api_secret: Raw API secret from the X-Api-Secret header.
        db: Pooled database connection.

    Returns:
        marketplace_api_keys row dict.

    Raises:
        HTTPException 401: If the key is not found or the secret does not match.
    """
    key_rec = await row(
        db,
        "SELECT * FROM marketplace_api_keys WHERE key_id=$1 AND is_active=true",
        x_api_key,
    )
    if not key_rec:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not verify_secret(x_api_secret, key_rec["secret_hash"]):
        raise HTTPException(status_code=401, detail="Invalid API secret")
    await db.execute(
        "UPDATE marketplace_api_keys SET last_used_at=NOW() WHERE key_id=$1",
        x_api_key,
    )
    return key_rec


async def get_engine_caller(
    authorization: str = Header(default=None),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Engine auth dependency: optional JWT.

    - No header → role='reader' (public access; object policy controls what's allowed).
    - Valid admin token → role from token.
    - Invalid token → 401.
    """
    if not authorization:
        return {"email": None, "role": "reader"}
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != BEARER_SCHEME:
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        payload = decode_token(token)
        role = payload.get("role", "reader")
        admin_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    admin = await row(db, "SELECT * FROM admin.v_admin_users WHERE id=$1 AND is_active=true", admin_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found or inactive")
    return admin


def pagination(skip: int = 0, limit: int = DEFAULT_QUERY_LIMIT) -> dict:
    """FastAPI dependency: parse and cap pagination parameters.

    Args:
        skip: Row offset (default 0).
        limit: Maximum rows to return (capped at MAX_QUERY_LIMIT).

    Returns:
        Dict with 'skip' and 'limit' keys.
    """
    return {"skip": skip, "limit": min(limit, MAX_QUERY_LIMIT)}
