"""
Module: toolkit.routers.data
Purpose: Thin HTTP handlers for generic data CRUD on any toolkit-managed table.
         All business logic is delegated to data_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.core.constants import SQL_MAX_ROWS, DEFAULT_QUERY_LIMIT
from app.modules.dbtoolkit.services import data_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/data", tags=["Toolkit - Data"])


@router.post("/{table_code}/list")
async def list_records(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return a page of records from the given table."""
    try:
        result = await data_service.list_records(
            db,
            table_code=table_code,
            search=body.get("search") or None,
            is_active=body.get("is_active"),
            limit=min(int(body.get("limit", DEFAULT_QUERY_LIMIT)), SQL_MAX_ROWS),
            offset=int(body.get("skip", 0)),
            lang=body.get("lang"),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("list_records(%s) failed", table_code)
        return err(str(e))


@router.post("/{table_code}/get")
async def get_record(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return a single record with its translations."""
    try:
        rid = body.get("id")
        if rid is None:
            return err("id required")
        result = await data_service.get_record(
            db, table_code=table_code, record_id=int(rid), lang=body.get("lang")
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("get_record(%s) failed", table_code)
        return err(str(e))


@router.post("/{table_code}/create")
async def create_record(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Insert a new record into the given table."""
    try:
        user   = admin.get("email", "system")
        result = await data_service.create_record(
            db, table_code=table_code, data=body, user=user, lang=body.get("lang")
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_record(%s) failed", table_code)
        return err(str(e))


@router.post("/{table_code}/update")
async def update_record(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update an existing record."""
    try:
        rid = body.get("id")
        if rid is None:
            return err("id required")
        user   = admin.get("email", "system")
        result = await data_service.update_record(
            db, table_code=table_code, record_id=int(rid), data=body,
            user=user, lang=body.get("lang")
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_record(%s) failed", table_code)
        return err(str(e))


@router.post("/{table_code}/delete")
async def delete_record(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete or soft-delete a record."""
    try:
        rid = body.get("id")
        if rid is None:
            return err("id required")
        result = await data_service.delete_record(
            db, table_code=table_code, record_id=int(rid),
            soft=body.get("soft", True),
            user=admin.get("email", "system"),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_record(%s) failed", table_code)
        return err(str(e))


@router.post("/{table_code}/ref-options")
async def ref_options(
    table_code: str,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return code/label pairs for use as reference dropdown options."""
    try:
        result = await data_service.get_ref_options(
            db,
            table_code=table_code,
            search=body.get("search") or None,
            limit=min(int(body.get("limit", 100)), SQL_MAX_ROWS),
            lang=body.get("lang"),
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("ref_options(%s) failed", table_code)
        return err(str(e))
