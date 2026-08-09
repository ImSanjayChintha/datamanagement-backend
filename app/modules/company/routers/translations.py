"""
Module: company.routers.translations
Purpose: HTTP handlers for company_translation endpoints.
         All business logic is in company.services.translation_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.company.services import translation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/company/translations", tags=["Company - Translations"])


@router.post("/list")
async def list_translations(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await translation_service.list_translations(
            db,
            search=body.get("search") or None,
            is_active=body.get("is_active"),
            limit=int(body.get("limit", 50)),
            offset=int(body.get("skip", 0)),
        )
        return ok(result)
    except Exception:
        logger.exception("list_translations failed")
        return err("Internal server error")


@router.post("/get")
async def get_translation(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await translation_service.get_translation(db, int(id)))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_translation failed")
        return err("Internal server error")


@router.post("/create")
async def create_translation(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await translation_service.create_translation(
            db, data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_translation failed")
        return err("Internal server error")


@router.post("/update")
async def update_translation(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await translation_service.update_translation(
            db, int(id), data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_translation failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_translation(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await translation_service.delete_translation(
            db, int(id), soft=body.get("soft", True)
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("delete_translation failed")
        return err("Internal server error")
