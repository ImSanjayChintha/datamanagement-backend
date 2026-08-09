"""
Module: toolkit.routers.schemas
Purpose: Thin HTTP handlers for PostgreSQL schema management.
         All business logic lives in schema_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import require_admin_role
from app.core.response import ok, err
from app.modules.dbtoolkit.services import schema_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/schemas", tags=["Toolkit - Schemas"])


@router.post("/list")
async def list_schemas(
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Return all user-visible schemas with table counts."""
    try:
        return ok(await schema_service.list_schemas(db))
    except Exception:
        logger.exception("list_schemas failed")
        return err("Internal server error")


@router.post("/get")
async def get_schema(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Return details and table list for a single schema."""
    try:
        name = body.get("name")
        if not name:
            return err("name is required")
        return ok(await schema_service.get_schema(db, name))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("get_schema failed")
        return err("Internal server error")


@router.post("/create")
async def create_schema(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Create a new PostgreSQL schema for a toolkit domain.

    Body:
        name        (str, required)  — schema name; must be lowercase letters,
                                       digits, underscores; max 63 chars.
        description (str, optional) — COMMENT ON SCHEMA text.

    Returns success even if the schema already exists (idempotent).
    """
    try:
        name = body.get("name")
        if not name:
            return err("name is required")
        return ok(await schema_service.create_schema(
            db,
            name=name,
            description=body.get("description"),
            user=admin["email"],
        ))
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("create_schema failed")
        return err("Internal server error")
