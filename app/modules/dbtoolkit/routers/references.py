"""
Module: toolkit.routers.references
Purpose: Thin HTTP handlers for toolkit field reference configuration.
         All business logic is delegated to reference_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import reference_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/references", tags=["Toolkit - References"])


@router.post("/get")
async def get_reference(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return the reference config for a field."""
    try:
        field_id = body.get("field_id")
        if field_id is None:
            return err("field_id required")
        result = await reference_service.get_reference(db, field_id=field_id)
        return ok(result)
    except Exception as e:
        logger.exception("get_reference failed")
        return err(str(e))


@router.post("/upsert")
async def upsert_reference(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Create or update the reference config for a field."""
    try:
        result = await reference_service.upsert_reference(db, data=body)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("upsert_reference failed")
        return err(str(e))


@router.post("/delete")
async def delete_reference(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete the reference config for a field."""
    try:
        field_id = body.get("field_id")
        if field_id is None:
            return err("field_id required")
        await reference_service.delete_reference(db, field_id=field_id)
        return ok({"deleted": True})
    except Exception as e:
        logger.exception("delete_reference failed")
        return err(str(e))


@router.post("/options")
async def fetch_reference_options(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Fetch dropdown options for a select/multiselect field."""
    try:
        field_id = body.get("field_id")
        if field_id is None:
            return err("field_id required")
        result = await reference_service.get_reference_options(
            db,
            field_id=field_id,
            search=body.get("search") or None,
            limit=int(body.get("limit", 100)),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("fetch_reference_options failed")
        return err(str(e))
