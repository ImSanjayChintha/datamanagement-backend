"""
Module: administration.routers.admin_users
Purpose: Thin HTTP handlers for admin user management endpoints.
         All business logic lives in administration.services.admin_user_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.administration.services import admin_user_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/users", tags=["Admin - Users"])


@router.post("/list")
async def list_admin_users(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await admin_user_service.list_users(
            db,
            role=body.get("role") or None,
            search=body.get("search") or None,
            skip=body.get("skip", 0),
            limit=body.get("limit", 50),
        )
        return ok(result)
    except Exception:
        logger.exception("list_admin_users failed")
        return err("Internal server error")


@router.post("/invite")
async def invite_admin_user(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await admin_user_service.invite_user(
            db, admin,
            body.get("email"),
            body.get("full_name"),
            body.get("role", "reader"),
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("invite_admin_user failed")
        return err("Internal server error")


@router.post("/resend-invite")
async def resend_admin_invite(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        target_id = body.get("id")
        if target_id is None:
            return err("id is required")
        return ok(await admin_user_service.resend_invite(db, target_id))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("resend_admin_invite failed")
        return err("Internal server error")


@router.post("/me")
async def get_me(admin: dict = Depends(get_current_admin)):
    try:
        safe = {"id", "email", "username", "full_name", "role", "permissions",
                "is_active", "must_change_password", "last_login_at", "created_at"}
        return ok({k: v for k, v in admin.items() if k in safe})
    except Exception:
        logger.exception("get_me failed")
        return err("Internal server error")


@router.post("/update")
async def update_admin_user(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        target_id = body.get("id")
        if target_id is None:
            return err("id is required")
        return ok(await admin_user_service.update_user(db, target_id, body))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_admin_user failed")
        return err("Internal server error")


@router.post("/reset-password")
async def reset_admin_password(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        target_id = body.get("id")
        if target_id is None:
            return err("id is required")
        return ok(await admin_user_service.reset_password(db, target_id))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("reset_admin_password failed")
        return err("Internal server error")


@router.post("/deactivate")
async def deactivate_admin_user(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        target_id = body.get("id")
        if target_id is None:
            return err("id is required")
        return ok(await admin_user_service.deactivate_user(db, admin, target_id))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("deactivate_admin_user failed")
        return err("Internal server error")
