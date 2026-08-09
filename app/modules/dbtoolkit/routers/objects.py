"""
Module: toolkit.routers.objects
Purpose: Thin HTTP handlers for toolkit_objects management.
         All business logic is delegated to object_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import object_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/objects", tags=["Toolkit - Objects"])


@router.post("/list")
async def list_objects(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return all active toolkit objects, optionally filtered by type."""
    try:
        result = await object_service.list_objects(db, object_type=body.get("object_type"))
        return ok(result)
    except Exception as e:
        logger.exception("list_objects failed")
        return err(str(e))


@router.post("/get")
async def get_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return a single toolkit object by id or code."""
    try:
        result = await object_service.get_object(
            db, id=body.get("id"), code=body.get("code")
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("get_object failed")
        return err(str(e))


@router.post("/create")
async def create_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Create and execute a new toolkit object."""
    try:
        user   = admin.get("email", "system")
        result = await object_service.create_object(db, data=body, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_object failed")
        return err(str(e))


@router.post("/update")
async def update_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update and re-execute a toolkit object."""
    try:
        oid = body.get("id")
        if not oid:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await object_service.update_object(db, object_id=oid, data=body, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_object failed")
        return err(str(e))


@router.post("/delete")
async def delete_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a toolkit object and drop it from the database."""
    try:
        oid = body.get("id")
        if not oid:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await object_service.delete_object(db, object_id=oid, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_object failed")
        return err(str(e))


@router.post("/reapply")
async def reapply_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Re-execute the stored SQL for a toolkit object."""
    try:
        oid = body.get("id")
        if not oid:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await object_service.reapply_object(db, object_id=oid, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("reapply_object failed")
        return err(str(e))
