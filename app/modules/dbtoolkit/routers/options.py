"""
Module: toolkit.routers.options
Purpose: Thin HTTP handlers for toolkit field option endpoints.
         All business logic is delegated to option_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import option_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/options", tags=["Toolkit - Field Options"])


@router.post("/list")
async def list_options(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return all options for an inline_select field."""
    try:
        field_id = body.get("field_id")
        if field_id is None:
            return err("field_id required")
        result = await option_service.list_options(db, field_id=field_id)
        return ok(result)
    except Exception as e:
        logger.exception("list_options failed")
        return err(str(e))


@router.post("/create")
async def create_option(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Create a new option for an inline_select field."""
    try:
        result = await option_service.create_option(db, data=body)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_option failed")
        return err(str(e))


@router.post("/update")
async def update_option(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update an existing option."""
    try:
        oid = body.get("id")
        if oid is None:
            return err("id required")
        result = await option_service.update_option(db, option_id=oid, data=body)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_option failed")
        return err(str(e))


@router.post("/delete")
async def delete_option(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete an option."""
    try:
        oid = body.get("id")
        if oid is None:
            return err("id required")
        await option_service.delete_option(db, option_id=oid)
        return ok({"deleted": True})
    except Exception as e:
        logger.exception("delete_option failed")
        return err(str(e))


@router.post("/reorder")
async def reorder_options(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update sort_order for a list of options."""
    try:
        await option_service.reorder_options(db, order=body.get("order") or [])
        return ok({"reordered": True})
    except Exception as e:
        logger.exception("reorder_options failed")
        return err(str(e))
