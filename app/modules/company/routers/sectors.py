"""
Module: company.routers.sectors
Purpose: HTTP handlers for company_sectors endpoints.
         All business logic is in company.services.sector_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.company.services import sector_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/company/sectors", tags=["Company - Sectors"])


@router.post("/list")
async def list_sectors(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await sector_service.list_sectors(
            db,
            search=body.get("search") or None,
            is_active=body.get("is_active"),
            parent_code=body.get("parent_code") or None,
            limit=int(body.get("limit", 50)),
            offset=int(body.get("skip", 0)),
        )
        return ok(result)
    except Exception:
        logger.exception("list_sectors failed")
        return err("Internal server error")


@router.post("/get")
async def get_sector(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await sector_service.get_sector(db, int(id)))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_sector failed")
        return err("Internal server error")


@router.post("/create")
async def create_sector(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await sector_service.create_sector(
            db, data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_sector failed")
        return err("Internal server error")


@router.post("/update")
async def update_sector(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await sector_service.update_sector(
            db, int(id), data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_sector failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_sector(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await sector_service.delete_sector(
            db, int(id), soft=body.get("soft", True)
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("delete_sector failed")
        return err("Internal server error")
