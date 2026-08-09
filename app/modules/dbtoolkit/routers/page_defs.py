"""
Module: toolkit.routers.page_defs
Purpose: HTTP handlers for dynamic page definitions (list/form configs).
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import page_def_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/page-defs", tags=["Toolkit - Page Defs"])


@router.post("/list")
async def list_page_defs(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await page_def_service.list_page_defs(
            db,
            nav_section=body.get("nav_section"),
            is_active=body.get("is_active"),
            search=body.get("search"),
            page=int(body.get("page") or 1),
            page_size=int(body.get("page_size") or 25),
        )
        return ok(result)
    except Exception as e:
        logger.exception("list_page_defs failed")
        return err(str(e))


@router.post("/get")
async def get_page_def(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await page_def_service.get_page_def(
            db,
            id=body.get("id"),
            code=body.get("code"),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("get_page_def failed")
        return err(str(e))


@router.post("/upsert")
async def upsert_page_def(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        user = admin.get("email", "system")
        result = await page_def_service.upsert_page_def(db, body, user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("upsert_page_def failed")
        return err(str(e))


@router.post("/delete")
async def delete_page_def(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        rec_id = body.get("id")
        if rec_id is None:
            return err("id is required")
        user = admin.get("email", "system")
        result = await page_def_service.delete_page_def(db, int(rec_id), user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_page_def failed")
        return err(str(e))
