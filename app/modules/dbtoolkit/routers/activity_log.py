"""
Module: toolkit.routers.activity_log
Purpose: HTTP handlers for the toolkit activity log.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.services import activity_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/activity-log", tags=["Toolkit - Activity Log"])


@router.post("/list")
async def list_activity(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return paginated activity log entries, newest first.

    Optional body filters:
      performed_by  — partial email match
      entity_type   — 'table', 'field', 'reference'
      activity_type — e.g. 'field_created', 'table_deleted'
      table_code    — filter by parent table
      schema_name   — filter by schema
      since         — ISO timestamp lower bound
      limit         — max rows (default 100, max 500)
      offset        — pagination offset
    """
    try:
        result = await activity_service.list_activity(
            db,
            performed_by=body.get("performed_by"),
            entity_type=body.get("entity_type"),
            activity_type=body.get("activity_type"),
            table_code=body.get("table_code"),
            schema_name=body.get("schema_name"),
            since=body.get("since"),
            limit=int(body.get("limit", 100)),
            offset=int(body.get("offset", 0)),
        )
        return ok(result)
    except Exception as e:
        logger.exception("list_activity failed")
        return err(str(e))
