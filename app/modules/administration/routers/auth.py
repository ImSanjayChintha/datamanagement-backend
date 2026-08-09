"""
Module: administration.routers.auth
Purpose: Thin HTTP handlers for authentication endpoints.
         All business logic lives in administration.services.auth_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.administration.services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/admin/login")
async def admin_login(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    try:
        return ok(await auth_service.admin_login(db, body.get("email"), body.get("password")))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("admin_login failed")
        return err("Internal server error")


@router.post("/admin/refresh")
async def admin_refresh(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    try:
        return ok(await auth_service.admin_refresh(db, body.get("refresh_token")))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("admin_refresh failed")
        return err("Internal server error")


@router.post("/admin/change-password")
async def admin_change_password(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        return ok(await auth_service.admin_change_password(
            db, admin, body.get("current_password"), body.get("new_password")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("admin_change_password failed")
        return err("Internal server error")


@router.post("/customer/register")
async def customer_register(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    try:
        return ok(await auth_service.customer_register(
            db,
            body.get("email"),
            body.get("password"),
            body.get("first_name"),
            body.get("last_name"),
            body.get("company_name"),
            body.get("account_type", "retail"),
            body.get("currency", "USD"),
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("customer_register failed")
        return err("Internal server error")


@router.post("/customer/login")
async def customer_login(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    try:
        return ok(await auth_service.customer_login(db, body.get("email"), body.get("password")))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("customer_login failed")
        return err("Internal server error")


@router.post("/customer/refresh")
async def customer_refresh(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    try:
        return ok(await auth_service.customer_refresh(db, body.get("refresh_token")))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("customer_refresh failed")
        return err("Internal server error")
