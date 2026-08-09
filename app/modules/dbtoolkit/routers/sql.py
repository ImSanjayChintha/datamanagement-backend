"""
Module: toolkit.routers.sql
Purpose: Thin HTTP handlers for the SQL Console.
         All business logic is delegated to sql_service.
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin, require_admin_role
from app.core.response import ok, err
from app.modules.dbtoolkit.services import sql_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/sql", tags=["Toolkit - SQL Console"])


@router.post("/validate")
async def validate_sql(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Validate SQL using PostgreSQL's own parser (rolled-back transaction)."""
    try:
        sql_text = (body.get("sql") or "").strip()
        if not sql_text:
            return err("sql is required")
        result = await sql_service.validate_sql(db, sql=sql_text)
        if result["valid"]:
            return ok(result)
        return err(result["message"])
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("validate_sql failed")
        return err(str(e))


@router.post("/execute")
async def execute_sql(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Execute SQL via the console with automatic DDL cataloguing."""
    try:
        sql        = (body.get("sql") or "").strip()
        table_code = (body.get("table_code") or "__console__").strip() or "__console__"
        ai_meta    = body.get("object_meta") or {}
        user       = admin.get("email", "system")

        if not sql:
            return err("sql is required")

        result = await sql_service.execute_sql(
            db, sql=sql, table_code=table_code, object_meta=ai_meta, user=user
        )
        from app.db.schema_export import schedule_schema_export
        schedule_schema_export()
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        try:
            return err(str(e))
        except Exception:
            logger.exception("execute_sql failed")
            return err("Execution failed")


@router.post("/history")
async def sql_history(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return DDL log entries."""
    try:
        result = await sql_service.get_sql_history(
            db,
            table_code=body.get("table_code") or None,
            operation=body.get("operation") or None,
            limit=min(int(body.get("limit", 50)), 200),
        )
        return ok(result)
    except Exception as e:
        logger.exception("sql_history failed")
        return err(str(e))
