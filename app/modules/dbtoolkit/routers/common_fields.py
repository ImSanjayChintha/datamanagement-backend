"""
Module: dbtoolkit.routers.common_fields
Purpose: HTTP handlers for toolkit.common_fields.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.dbtoolkit.services import common_fields_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/common-fields", tags=["Toolkit - Common Fields"])


@router.post("/list")
async def list_common_fields(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await common_fields_service.list_common_fields(
            db,
            search=body.get("search") or None,
            is_active=body.get("is_active"),
            field_role=body.get("field_role") or None,
            limit=int(body.get("limit", 100)),
            offset=int(body.get("skip", 0)),
        )
        return ok(result)
    except Exception:
        logger.exception("list_common_fields failed")
        return err("Internal server error")


@router.post("/get")
async def get_common_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await common_fields_service.get_common_field(db, int(id)))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_common_field failed")
        return err("Internal server error")


@router.post("/create")
async def create_common_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await common_fields_service.create_common_field(
            db, data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_common_field failed")
        return err("Internal server error")


@router.post("/update")
async def update_common_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await common_fields_service.update_common_field(
            db, int(id), data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_common_field failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_common_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await common_fields_service.delete_common_field(
            db, int(id), soft=body.get("soft", True)
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("delete_common_field failed")
        return err("Internal server error")


