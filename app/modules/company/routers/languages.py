"""
Module: company.routers.languages
Purpose: HTTP handlers for company_languages endpoints.
         All business logic is in company.services.language_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.company.services import language_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/company/languages", tags=["Company - Languages"])


@router.post("/list")
async def list_languages(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        result = await language_service.list_languages(db)
        return ok(result)
    except Exception:
        logger.exception("list_languages failed")
        return err("Internal server error")


@router.post("/get")
async def get_language(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await language_service.get_language(db, int(id)))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_language failed")
        return err("Internal server error")


@router.post("/create")
async def create_language(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        return ok(await language_service.create_language(
            db, data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_language failed")
        return err("Internal server error")


@router.post("/update")
async def update_language(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await language_service.update_language(
            db, int(id), data=body, user=admin.get("email", "system")
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("update_language failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_language(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    try:
        id = body.get("id")
        if id is None:
            return err("id is required")
        return ok(await language_service.delete_language(
            db, int(id), soft=body.get("soft", True)
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("delete_language failed")
        return err("Internal server error")
