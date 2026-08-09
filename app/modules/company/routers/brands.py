"""
Module: company.routers.brands
Purpose: HTTP handlers for company_brands endpoints.
         All business logic is in company.services.brand_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.company.services import brand_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/company/brands", tags=["Company - Brands"])


@router.post("/list")
async def list_brands(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await brand_service.list_brands(
            db,
            search=body.get("search") or None,
            is_active=body.get("is_active"),
            limit=int(body.get("limit", 50)),
            offset=int(body.get("skip", 0)),
        )
        return ok(result)
    except Exception:
        logger.exception("list_brands failed")
        return err("Internal server error")


@router.post("/get")
async def get_brand(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await brand_service.get_brand(db, int(id)))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_brand failed")
        return err("Internal server error")


@router.post("/create")
async def create_brand(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await brand_service.create_brand(
            db, data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_brand failed")
        return err("Internal server error")


@router.post("/update")
async def update_brand(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await brand_service.update_brand(
            db, int(id), data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_brand failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_brand(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await brand_service.delete_brand(
            db, int(id), soft=body.get("soft", True)
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("delete_brand failed")
        return err("Internal server error")
