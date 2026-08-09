"""
Module: toolkit.routers.tables
Purpose: Thin HTTP handlers for toolkit table management endpoints.

All business logic is delegated to table_service.  Each handler:
  1. Parses the request body.
  2. Calls the appropriate service function.
  3. Returns ok() or err().
"""
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.core.ddl import build_full_ddl
from app.modules.dbtoolkit.services import table_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/tables", tags=["Toolkit - Tables"])

# ---------------------------------------------------------------------------
# Re-export conn_set_user so objects.py can still import it from here
# (backward-compat — prefer importing from db.executor directly in new code)
# ---------------------------------------------------------------------------
from app.modules.dbtoolkit.db.executor import conn_set_user  # noqa: F401, E402


@router.post("/list")
async def list_tables(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return all toolkit tables with field counts."""
    try:
        result = await table_service.list_tables(db, is_active=body.get("is_active"))
        return ok(result)
    except Exception as e:
        logger.exception("list_tables failed")
        return err(str(e))


@router.post("/get")
async def get_table(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return a single table with its fields and options."""
    try:
        tid = body.get("id")
        code = body.get("code")
        if tid is None and code is None:
            return err("id or code required")
        result = await table_service.get_table(
            db,
            id=tid if isinstance(tid, int) else None,
            code=code if isinstance(code, str) else None,
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("get_table failed")
        return err(str(e))


@router.post("/schema-fields")
async def schema_fields(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return column metadata for any schema.table_name directly from information_schema.
    Used by the page-def editor when the table code is a fully-qualified name like pim.attributes.
    Accepts: { schema_table: "pim.attributes" }
    Returns: { fields: [...] } — same shape as get_table.fields so the UI can reuse the same code.
    """
    try:
        schema_table = (body.get("schema_table") or "").strip()
        if not schema_table or "." not in schema_table:
            return err("schema_table must be in the form schema.table_name (e.g. pim.attributes)")

        schema_name, table_name = schema_table.split(".", 1)

        # ── data_type → toolkit field_type mapping ──────────────────────────
        PG_TO_FIELD_TYPE = {
            "text": "text", "character varying": "text", "character": "text", "name": "text",
            "integer": "number", "bigint": "number", "smallint": "number",
            "numeric": "number", "real": "number", "double precision": "number",
            "boolean": "boolean",
            "timestamp with time zone": "datetime", "timestamp without time zone": "datetime",
            "date": "date", "time without time zone": "time", "time with time zone": "time",
            "jsonb": "jsonb", "json": "jsonb",
            "uuid": "uuid",
            "ARRAY": "jsonb",
        }
        SYSTEM_COLUMNS = {"id", "inserted_at", "inserted_by", "modified_at", "modified_by"}

        col_rows = await db.fetch(
            """SELECT column_name, data_type, is_nullable, ordinal_position
               FROM information_schema.columns
               WHERE table_schema = $1 AND table_name = $2
               ORDER BY ordinal_position""",
            schema_name, table_name,
        )

        if not col_rows:
            return err(f"No columns found for {schema_table} — check the schema and table name")

        fields = []
        for i, col in enumerate(col_rows):
            col_name = col["column_name"]
            pg_type  = col["data_type"]
            ft       = PG_TO_FIELD_TYPE.get(pg_type, "text")
            is_sys   = col_name in SYSTEM_COLUMNS
            fields.append({
                "id":              -(i + 1),         # negative sentinel — not a real toolkit field
                "table_id":        0,
                "table_code":      schema_table,
                "code":            col_name,
                "label":           col_name.replace("_", " ").title(),
                "description":     "",
                "field_type":      ft,
                "is_required":     col["is_nullable"] == "NO",
                "is_unique":       False,
                "is_multilingual": False,
                "is_system":       is_sys,
                "config":          {},
                "default_value":   None,
                "sort_order":      col["ordinal_position"],
                "is_active":       True,
                "ref_table_id":    None,
                "ref_table_code":  None,
                "ref_table_label": None,
                "store_field":     None,
                "display_field":   None,
                "display_template":None,
                "search_fields":   None,
                "filter_conditions":None,
                "order_by":        None,
                "ref_type":        None,
                "cascade_on_delete":None,
            })

        return ok({"fields": fields, "options": [], "schema_table": schema_table})

    except Exception as e:
        logger.exception("schema_fields failed")
        return err(str(e))


@router.post("/preview-ddl")
async def preview_ddl(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return DDL SQL preview without executing it."""
    try:
        table_def = body.get("table")
        fields    = body.get("fields", [])
        if not table_def or not table_def.get("code"):
            return err("table.code is required")
        ref_map, schema_map = await table_service.fetch_ref_fields(db, fields)
        ddl_parts = build_full_ddl(table_def, fields, ref_fields_map=ref_map, ref_schema_map=schema_map)
        return ok({"sql": "\n\n".join(sql for _, sql in ddl_parts)})
    except Exception as e:
        logger.exception("preview_ddl failed")
        return err(str(e))


@router.post("/ddl")
async def get_ddl(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return the DDL SQL for an existing catalog table.

    create_table is always builder-generated (source of truth for the schema).
    create_view and create_function are fetched from the live database so the
    tab always shows what is actually deployed, not what the builder would generate.
    """
    try:
        from app.core.database import row, rows
        tid  = body.get("id")
        code = body.get("code")
        if tid is not None:
            tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", tid)
        elif code:
            tbl = await row(db, "SELECT * FROM toolkit_tables WHERE code=$1", code)
        else:
            return err("id or code required")
        if not tbl:
            return err("Table not found")
        flds      = await rows(
            db,
            "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order, code",
            tbl["id"],
        )
        flds_list            = [dict(f) for f in flds]
        ref_map, schema_map  = await table_service.fetch_ref_fields(db, flds_list)
        ddl_parts = build_full_ddl(dict(tbl), flds_list, ref_fields_map=ref_map, ref_schema_map=schema_map)

        _schema = (tbl.get("schema_name") or "toolkit").lower()
        _code   = tbl["code"].lower()

        # For view and list function, replace builder output with actual DB definition
        actual_parts = []
        for operation, sql in ddl_parts:
            if operation == "create_view":
                actual = await db.fetchval(
                    """
                    SELECT 'CREATE OR REPLACE VIEW '
                           || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                           || E' AS\n' || pg_get_viewdef(c.oid, true)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'v'
                    """,
                    _schema, f"v_{_code}",
                )
                actual_parts.append((operation, actual or sql))
            elif operation == "create_function":
                actual = await db.fetchval(
                    """
                    SELECT pg_get_functiondef(p.oid)
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = $1 AND p.proname = $2
                    LIMIT 1
                    """,
                    _schema, f"fn_list_{_code}",
                )
                actual_parts.append((operation, actual or sql))
            else:
                actual_parts.append((operation, sql))

        # Look up any previously applied upsert / sync / delete functions in toolkit_objects
        saved_functions: dict = {}
        for fn_type in ("upsert", "sync", "delete"):
            fn_code = f"{fn_type}_{_code}"
            saved_row = await db.fetchrow(
                "SELECT sql, metadata_json FROM toolkit_objects "
                "WHERE schema_name=$1 AND code=$2 AND object_type='function' AND is_active=TRUE",
                _schema, fn_code,
            )
            if saved_row:
                meta = saved_row["metadata_json"] or {}
                saved_functions[fn_type] = {
                    "sql":  saved_row["sql"],
                    "keys": meta.get("keys", []) if isinstance(meta, dict) else [],
                }

        return ok({
            "table_code":      tbl["code"],
            "sql":             "\n\n".join(sql for _, sql in actual_parts),
            "parts":           [{"operation": op, "sql": sql} for op, sql in actual_parts],
            "saved_functions": saved_functions,
        })
    except Exception as e:
        logger.exception("get_ddl failed")
        return err(str(e))


@router.post("/create")
async def create_table(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Create a new toolkit-managed table and execute DDL."""
    try:
        user   = admin.get("email", "system")
        result = await table_service.create_table(
            db,
            table=body,
            fields=body.get("fields") or [],
            options=body.get("options") or [],
            user=user,
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("create_table failed")
        return err(str(e))


@router.post("/update")
async def update_table(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update a table's metadata and fields."""
    try:
        tid = body.get("id")
        if tid is None:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await table_service.update_table(
            db,
            table_id=tid,
            table=body,
            fields=body.get("fields") or [],
            options=body.get("options") or [],
            user=user,
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("update_table failed")
        return err(str(e))


@router.post("/save-translations")
async def save_translations(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Save only field label/i18n translations — no DDL, no schema changes."""
    try:
        table_id = body.get("id")
        if table_id is None:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await table_service.save_table_translations(
            db, int(table_id), body.get("fields") or [], user,
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("save_translations failed for table %s", body.get("id"))
        return err("Internal server error")


@router.post("/associated-objects")
async def associated_objects(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return views, functions, and API endpoints linked to a table — used before deletion."""
    try:
        tid = body.get("id")
        if tid is None:
            return err("id is required")
        result = await table_service.get_associated_objects(db, table_id=tid)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception:
        logger.exception("associated_objects failed")
        return err("Internal server error")


@router.post("/delete")
async def delete_table(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a toolkit table and all associated DB objects."""
    try:
        tid = body.get("id")
        if tid is None:
            return err("id is required")
        user   = admin.get("email", "system")
        result = await table_service.delete_table(db, table_id=tid, user=user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("delete_table failed")
        return err(str(e))


@router.post("/regenerate")
async def regenerate_view(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Re-create the view and/or list function for a table.

    Pass object_type='view' to regenerate only the view,
    object_type='function' for only the list function,
    or omit to regenerate both.
    """
    try:
        tid = body.get("id") or body.get("table_id")
        if tid is None:
            return err("id required")
        user        = admin.get("email", "system")
        object_type = body.get("object_type") or None  # 'view' | 'function' | None
        result = await table_service.regenerate_view(db, table_id=tid, user=user, object_type=object_type)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("regenerate_view failed")
        return err(str(e))


@router.post("/reset-object")
async def reset_object(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a user-customised view/function from toolkit_objects and re-apply
    the auto-generated version from the field catalog."""
    try:
        tid         = body.get("id") or body.get("table_id")
        object_code = (body.get("object_code") or "").strip()
        object_type = (body.get("object_type") or "").strip()
        if not tid:
            return err("id required")
        if not object_code or not object_type:
            return err("object_code and object_type are required")
        user   = admin.get("email", "system")
        result = await table_service.reset_object(db, tid, object_code, object_type, user)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("reset_object failed")
        return err(str(e))


@router.post("/regenerate-all")
async def regenerate_all(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Re-create view + all function wrappers for every active toolkit table."""
    try:
        user   = admin.get("email", "system")
        result = await table_service.regenerate_all_tables(db, user)
        return ok(result)
    except Exception as e:
        logger.exception("regenerate_all failed")
        return err(str(e))



@router.post("/schema-context")
async def schema_context(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return full schema context for AI (tables + objects metadata)."""
    try:
        codes  = body.get("codes")
        result = await table_service.get_schema_context(db, codes=codes)
        return ok(result)
    except Exception as e:
        logger.exception("schema_context failed")
        return err(str(e))
