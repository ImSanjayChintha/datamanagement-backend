"""
Module: toolkit.routers.fields
Purpose: Thin HTTP handlers for toolkit field management endpoints.
         All business logic is delegated to field_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import field_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/fields", tags=["Toolkit - Fields"])


@router.post("/list")
async def list_fields(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return ordered fields for a table."""
    try:
        result = await field_service.list_fields(
            db,
            table_id=body.get("table_id"),
            table_code=body.get("table_code"),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("list_fields failed")
        return err(str(e))


@router.post("/create")
async def create_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Add a new field to a toolkit table."""
    try:
        user   = admin.get("email", "system")
        result = await field_service.create_field(db, field_data=body, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_field failed")
        return err(str(e))


@router.post("/update")
async def update_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update a field's metadata."""
    try:
        fid = body.get("id")
        if fid is None:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await field_service.update_field(db, field_id=fid, field_data=body, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_field failed")
        return err(str(e))


@router.post("/reorder")
async def reorder_fields(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update sort_order for a list of fields."""
    try:
        await field_service.reorder_fields(db, order=body.get("order") or [])
        return ok({"reordered": True})
    except Exception as e:
        logger.exception("reorder_fields failed")
        return err(str(e))


@router.post("/delete")
async def delete_field(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a field and its physical column."""
    try:
        fid = body.get("id")
        if fid is None:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await field_service.delete_field(db, field_id=fid, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_field failed")
        return err(str(e))
