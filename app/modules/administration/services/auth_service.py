"""
Module: administration.services.auth_service
Purpose: Business logic for admin and customer authentication — login, token
         refresh, and password management.

All database operations go through toolkit views and functions — no raw SQL.
"""
import logging
import random
import string

import asyncpg
from jose import JWTError

from app.core.database import row, execute
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
)

logger = logging.getLogger(__name__)


async def admin_login(db: asyncpg.Connection, email: str, password: str) -> dict:
    admin = await row(db, "SELECT * FROM admin.v_admin_users WHERE email=$1 AND is_active=TRUE", email)
    if not admin or not verify_password(password, admin["hashed_password"]):
        raise ValueError("Invalid credentials")

    await execute(db, "SELECT admin.fn_touch_admin_login($1::bigint)", admin["id"])

    return {
        "access_token":         create_access_token(str(admin["id"]), admin["role"], {"email": admin["email"]}),
        "refresh_token":        create_refresh_token(str(admin["id"])),
        "token_type":           "bearer",
        "role":                 admin["role"],
        "admin_id":             admin["id"],
        "full_name":            admin["full_name"],
        "must_change_password": bool(admin.get("must_change_password", False)),
    }


async def admin_refresh(db: asyncpg.Connection, refresh_token: str) -> dict:
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise ValueError("Invalid refresh token")

    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")

    admin = await row(db, "SELECT * FROM admin.v_admin_users WHERE id=$1 AND is_active=TRUE", int(payload.get("sub")))
    if not admin:
        raise ValueError("Admin not found or inactive")

    return {
        "access_token": create_access_token(str(admin["id"]), admin["role"], {"email": admin["email"]}),
        "token_type":   "bearer",
    }


async def admin_change_password(
    db: asyncpg.Connection,
    admin: dict,
    current_password: str | None,
    new_password: str,
) -> dict:
    if not new_password or len(new_password) < 8:
        raise ValueError("new_password must be at least 8 characters")
    if not admin.get("must_change_password"):
        if not current_password:
            raise ValueError("current_password is required")
        if not verify_password(current_password, admin["hashed_password"]):
            raise ValueError("Current password is incorrect")

    new_hash = get_password_hash(new_password)
    await execute(db, "SELECT admin.fn_change_admin_password($1::bigint,$2)", admin["id"], new_hash)
    return {"changed": True}


async def customer_register(
    db: asyncpg.Connection,
    email: str,
    password: str,
    first_name: str | None,
    last_name: str | None,
    company_name: str | None,
    account_type: str = "retail",
    currency: str = "USD",
) -> dict:
    existing = await row(db, "SELECT id FROM v_customers WHERE email=$1", email)
    if existing:
        raise ValueError("Email already registered")

    code   = "CUST" + "".join(random.choices(string.digits, k=6))
    hashed = get_password_hash(password)

    cust = await row(
        db,
        "SELECT * FROM fn_register_customer($1,$2,$3,$4,$5,$6,$7,$8)",
        code, email, hashed, first_name, last_name, company_name, account_type, currency,
    )
    return {
        "access_token":  create_access_token(str(cust["id"]), "customer"),
        "refresh_token": create_refresh_token(str(cust["id"])),
        "token_type":    "bearer",
        "customer_id":   cust["id"],
    }


async def customer_login(db: asyncpg.Connection, email: str, password: str) -> dict:
    cust = await row(db, "SELECT * FROM v_customers WHERE email=$1", email)
    if not cust or not verify_password(password, cust["hashed_password"]):
        raise ValueError("Invalid credentials")
    return {
        "access_token":  create_access_token(str(cust["id"]), "customer"),
        "refresh_token": create_refresh_token(str(cust["id"])),
        "token_type":    "bearer",
        "customer_id":   cust["id"],
    }


async def customer_refresh(db: asyncpg.Connection, refresh_token: str) -> dict:
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise ValueError("Invalid refresh token")
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")

    cust = await row(db, "SELECT * FROM v_customers WHERE id=$1", int(payload.get("sub")))
    if not cust:
        raise ValueError("Customer not found")
    return {"access_token": create_access_token(str(cust["id"]), "customer"), "token_type": "bearer"}
