import logging
import asyncpg
from fastapi import APIRouter, Body, Depends

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateway/schema", tags=["Gateway"])

_EXCLUDED = ("pg_catalog", "information_schema", "pg_toast", "api_gateway")


@router.post("/schemas")
async def list_schemas(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        rows = await db.fetch("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast','api_gateway')
              AND schema_name NOT LIKE 'pg_%'
            ORDER BY schema_name
        """)
        return ok([r["schema_name"] for r in rows])
    except Exception as exc:
        return err(str(exc))


@router.post("/objects")
async def list_objects(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    schema = body.get("schema", "")
    if not schema:
        return ok([])
    try:
        rows = await db.fetch("""
            SELECT table_name AS name,
                   CASE WHEN table_type = 'BASE TABLE' THEN 'table' ELSE 'view' END AS type
            FROM information_schema.tables
            WHERE table_schema = $1
            ORDER BY table_type DESC, table_name
        """, schema)
        fn_rows = await db.fetch("""
            SELECT routine_name AS name, 'function' AS type
            FROM information_schema.routines
            WHERE routine_schema = $1 AND routine_type = 'FUNCTION'
            ORDER BY routine_name
        """, schema)
        return ok([dict(r) for r in rows] + [dict(r) for r in fn_rows])
    except Exception as exc:
        return err(str(exc))


@router.post("/columns")
async def list_columns(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    schema  = body.get("schema", "")
    obj     = body.get("object", "")
    obj_type = body.get("type", "")
    if not schema or not obj:
        return ok([])
    try:
        if obj_type == "function":
            return ok(await _function_columns(db, schema, obj))
        # table or view
        rows = await db.fetch("""
            SELECT column_name          AS name,
                   data_type            AS type,
                   (is_nullable = 'YES') AS nullable
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """, schema, obj)
        return ok([dict(r) for r in rows])
    except Exception as exc:
        return err(str(exc))


@router.post("/object-def")
async def get_object_def(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return the definition, columns, and source for any table/view/function."""
    schema  = (body.get("schema") or "").strip()
    name    = (body.get("name")   or "").strip()
    db_type = (body.get("type")   or "").strip()
    if not schema or not name or not db_type:
        return ok(None)
    try:
        if db_type == "function":
            row = await db.fetchrow("""
                SELECT
                    pg_get_functiondef(p.oid)        AS definition,
                    pg_get_function_result(p.oid)    AS return_type,
                    pg_get_function_arguments(p.oid) AS arguments,
                    p.prosrc                         AS source
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE n.nspname = $1 AND p.proname = $2
                ORDER BY p.oid DESC
                LIMIT 1
            """, schema, name)
            if not row:
                return ok(None)
            return ok({
                "type":        "function",
                "definition":  row["definition"],
                "return_type": row["return_type"],
                "arguments":   row["arguments"],
                "source":      row["source"],
                "columns":     None,
            })
        elif db_type == "view":
            col_rows = await db.fetch("""
                SELECT column_name AS name,
                       data_type   AS type,
                       (is_nullable = 'YES') AS nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """, schema, name)
            view_row = await db.fetchrow("""
                SELECT definition FROM pg_views
                WHERE schemaname = $1 AND viewname = $2
            """, schema, name)
            return ok({
                "type":       "view",
                "definition": view_row["definition"] if view_row else None,
                "columns":    [dict(r) for r in col_rows],
                "source":     None,
            })
        else:  # table
            col_rows = await db.fetch("""
                SELECT column_name AS name,
                       data_type   AS type,
                       (is_nullable = 'YES') AS nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """, schema, name)
            return ok({
                "type":    "table",
                "columns": [dict(r) for r in col_rows],
                "source":  None,
            })
    except Exception as exc:
        logger.error("get_object_def %s.%s (%s): %s", schema, name, db_type, exc)
        return err(str(exc))


@router.post("/table-data")
async def get_table_data(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return paginated rows from any table or view with optional per-column filters."""
    import datetime, uuid as _uuid

    schema  = (body.get("schema") or "").strip()
    table   = (body.get("table")  or "").strip()
    page    = max(1, int(body.get("page",  1)))
    limit   = min(500, max(1, int(body.get("limit", 50))))
    filters = body.get("filters") or {}

    if not schema or not table:
        return ok({"rows": [], "total": 0, "columns": []})

    # Validate existence via information_schema (prevents injecting arbitrary names)
    exists = await db.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = $1 AND table_name = $2
        )
    """, schema, table)
    if not exists:
        return err(f"{schema}.{table} not found")

    col_rows = await db.fetch("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
    """, schema, table)
    columns = [r["column_name"] for r in col_rows]
    valid_cols = set(columns)

    # Build safe parameterised WHERE clause
    where_parts: list[str] = []
    params: list = []
    for col, val in filters.items():
        if col not in valid_cols or val is None or str(val).strip() == "":
            continue
        params.append(f"%{val}%")
        where_parts.append(f'"{col}"::text ILIKE ${len(params)}')

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    offset    = (page - 1) * limit

    total = await db.fetchval(
        f'SELECT COUNT(*) FROM "{schema}"."{table}" {where_sql}', *params
    )
    rows = await db.fetch(
        f'SELECT * FROM "{schema}"."{table}" {where_sql} LIMIT {limit} OFFSET {offset}',
        *params,
    )

    def _ser(v):
        if v is None:                         return None
        if isinstance(v, bool):               return v
        if isinstance(v, (int, float)):       return v
        if isinstance(v, (dict, list)):       return v
        if isinstance(v, datetime.datetime):  return v.isoformat()
        if isinstance(v, datetime.date):      return v.isoformat()
        return str(v)

    return ok({
        "rows":    [{k: _ser(v) for k, v in dict(r).items()} for r in rows],
        "total":   int(total or 0),
        "columns": columns,
    })


@router.post("/function-def")
async def get_function_def(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return pg_proc metadata for a single function (source, return type, argument list)."""
    schema = (body.get("schema") or "").strip()
    name   = (body.get("name")   or "").strip()
    if not schema or not name:
        return ok(None)
    try:
        row = await db.fetchrow("""
            SELECT
                pg_get_functiondef(p.oid)        AS definition,
                pg_get_function_result(p.oid)    AS return_type,
                pg_get_function_arguments(p.oid) AS arguments,
                p.prosrc                         AS source
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = $1 AND p.proname = $2
            ORDER BY p.oid DESC
            LIMIT 1
        """, schema, name)
        if not row:
            return ok(None)
        return ok({
            "definition":  row["definition"],
            "return_type": row["return_type"],
            "arguments":   row["arguments"],
            "source":      row["source"],
        })
    except Exception as exc:
        logger.error("get_function_def %s.%s: %s", schema, name, exc)
        return err(str(exc))


async def _function_columns(db: asyncpg.Connection, schema: str, func_name: str) -> list[dict]:
    # Case 1: RETURNS TABLE(col1 type1, ...) — proargmodes has 't' entries
    rows = await db.fetch("""
        SELECT
            COALESCE(p.proargnames[gs.n], 'column_' || gs.n) AS name,
            format_type(
                (COALESCE(p.proallargtypes, p.proargtypes::oid[]))[gs.n],
                NULL
            ) AS type,
            TRUE AS nullable
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid,
        generate_subscripts(
            COALESCE(p.proallargtypes, p.proargtypes::oid[]), 1
        ) AS gs(n)
        WHERE n.nspname = $1
          AND p.proname = $2
          AND p.proargmodes IS NOT NULL
          AND p.proargmodes[gs.n] = 't'
        ORDER BY gs.n
        LIMIT 200
    """, schema, func_name)

    if rows:
        return [dict(r) for r in rows]

    # Case 2: RETURNS SETOF composite_type — look up that type's columns
    rows = await db.fetch("""
        SELECT
            a.attname                                 AS name,
            format_type(a.atttypid, a.atttypmod)      AS type,
            (NOT a.attnotnull)                        AS nullable
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        JOIN pg_type rt     ON rt.oid = p.prorettype
        JOIN pg_class c     ON c.oid  = rt.typrelid
        JOIN pg_attribute a ON a.attrelid = c.oid
                            AND a.attnum > 0
                            AND NOT a.attisdropped
        WHERE n.nspname = $1
          AND p.proname = $2
          AND p.proretset = TRUE
        ORDER BY a.attnum
        LIMIT 200
    """, schema, func_name)

    return [dict(r) for r in rows]
