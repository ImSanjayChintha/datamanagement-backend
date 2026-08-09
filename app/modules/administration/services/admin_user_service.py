"""
Module: administration.services.admin_user_service
Purpose: Business logic for admin user management — listing, inviting, updating,
         and deactivating admin accounts.

All database operations go through toolkit views and functions — no raw SQL.
"""
import logging
import secrets

import asyncpg

from app.core.config import settings
from app.core.database import row, rows, execute, val
from app.core.email import send_event_email
from app.core.security import get_password_hash
from app.modules.administration.core.constants import VALID_ROLES
from app.modules.administration.utils.password import generate_temp_password

logger = logging.getLogger(__name__)


async def list_users(
    db: asyncpg.Connection,
    role: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[dict]:
    limit = min(limit, 500)
    return await rows(db, "SELECT * FROM admin.fn_list_admin_users($1,$2,$3,$4)", role, limit, skip, search)


async def invite_user(
    db: asyncpg.Connection,
    inviting_admin: dict,
    email: str,
    full_name: str | None,
    role: str = "reader",
) -> dict:
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()

    if not email:
        raise ValueError("email is required")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")
    if await val(db, "SELECT id FROM admin.v_admin_users WHERE email=$1", email):
        raise ValueError("Email already registered")

    temp_password = generate_temp_password()
    hashed        = get_password_hash(temp_password)
    invite_token  = secrets.token_urlsafe(32)
    display_name  = full_name or email.split("@")[0]

    user = await row(
        db,
        "SELECT * FROM admin.fn_create_admin_user($1,$2,$3,$4,$5)",
        email, display_name, hashed, role, invite_token,
    )

    login_url = f"{settings.FRONTEND_URL}/login"
    try:
        await send_event_email(db, "admin_user_invited", email, {
            "full_name":     display_name,
            "email":         email,
            "role":          role.replace("_", " ").title(),
            "temp_password": temp_password,
            "login_url":     login_url,
        })
    except Exception as exc:
        logger.warning("Invite email failed for %s: %s", email, exc)

    return dict(user)


async def resend_invite(
    db: asyncpg.Connection,
    target_id: int,
) -> dict:
    target = await row(db, "SELECT * FROM admin.fn_get_admin_users($1::bigint)", target_id)
    if not target:
        raise ValueError("Admin user not found")
    if not target.get("must_change_password"):
        raise ValueError("User has already accepted their invite")

    temp_password = generate_temp_password()
    hashed        = get_password_hash(temp_password)
    invite_token  = secrets.token_urlsafe(32)

    await execute(db, "SELECT admin.fn_resend_admin_invite($1::bigint,$2,$3)", target_id, hashed, invite_token)

    login_url = f"{settings.FRONTEND_URL}/login"
    try:
        await send_event_email(db, "admin_user_invited", target["email"], {
            "full_name":     target["full_name"] or target["email"],
            "email":         target["email"],
            "role":          target["role"].replace("_", " ").title(),
            "temp_password": temp_password,
            "login_url":     login_url,
        })
    except Exception as exc:
        logger.warning("Resend invite failed for %s: %s", target["email"], exc)

    return {"resent": True}


async def update_user(
    db: asyncpg.Connection,
    target_id: int,
    body: dict,
) -> dict:
    updates: dict = {}
    for f in ("full_name", "is_active"):
        if f in body:
            updates[f] = body[f]
    if "role" in body:
        if body["role"] not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")
        updates["role"] = body["role"]

    if not updates:
        raise ValueError("No updatable fields provided")

    updated = await row(
        db,
        "SELECT * FROM admin.fn_update_admin_user($1::bigint,$2,$3,$4)",
        target_id,
        updates.get("full_name"),
        updates.get("is_active"),
        updates.get("role"),
    )
    if not updated:
        raise ValueError("Admin user not found")
    return dict(updated)


async def reset_password(
    db: asyncpg.Connection,
    target_id: int,
) -> dict:
    target = await row(db, "SELECT * FROM admin.fn_get_admin_users($1::bigint)", target_id)
    if not target:
        raise ValueError("Admin user not found")

    temp_password = generate_temp_password()
    new_hash      = get_password_hash(temp_password)
    await execute(db, "SELECT admin.fn_reset_admin_password($1::bigint,$2)", target_id, new_hash)

    login_url = f"{settings.FRONTEND_URL}/login"
    try:
        await send_event_email(db, "admin_user_invited", target["email"], {
            "full_name":     target["full_name"] or target["email"],
            "email":         target["email"],
            "role":          target["role"].replace("_", " ").title(),
            "temp_password": temp_password,
            "login_url":     login_url,
        })
    except Exception as exc:
        logger.warning("Reset password email failed for %s: %s", target["email"], exc)

    return {"reset": True}


async def deactivate_user(
    db: asyncpg.Connection,
    requesting_admin: dict,
    target_id: int,
) -> dict:
    if target_id == requesting_admin.get("id"):
        raise ValueError("Cannot deactivate your own account")
    await execute(db, "SELECT admin.fn_deactivate_admin_user($1::bigint)", target_id)
    return {"deactivated": True}
