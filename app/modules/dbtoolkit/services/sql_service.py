"""
Module: toolkit.services.sql_service
Purpose: SQL Console business logic — validation, execution, history, and
         automatic toolkit_objects catalog integration for DDL statements.

The SQL Console allows admin users to run arbitrary SQL against the database
with safety guardrails (blocked patterns, row limits).  DDL statements that
create views or functions are automatically catalogued in toolkit_objects.
"""
import logging
import re

import asyncpg

from app.core.database import row, rows, execute
from app.modules.dbtoolkit.core.constants import DEFAULT_SCHEMA, SQL_MAX_ROWS, SQL_BLOCKED_PATTERNS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for DDL detection
# ---------------------------------------------------------------------------

_DDL_VIEW_RE     = re.compile(r'create\s+(?:or\s+replace\s+)?view\s+(?:(\w+)\.)?(\w+)', re.IGNORECASE)
_DDL_FUNCTION_RE = re.compile(r'create\s+(?:or\s+replace\s+)?function\s+(?:(\w+)\.)?(\w+)', re.IGNORECASE)


class _RollbackSentinel(Exception):
    """Raised intentionally to trigger a transaction rollback after a dry-run."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_ddl_object(sql: str) -> tuple[str | None, str | None, str | None]:
    """Return (object_type, schema_name, code) for the first CREATE VIEW/FUNCTION.

    Args:
        sql: SQL string to inspect.

    Returns:
        Tuple of (object_type, schema_name, code) or (None, None, None).
    """
    m = _DDL_VIEW_RE.search(sql)
    if m:
        return "view", (m.group(1) or DEFAULT_SCHEMA).lower(), m.group(2).lower()
    m = _DDL_FUNCTION_RE.search(sql)
    if m:
        return "function", (m.group(1) or DEFAULT_SCHEMA).lower(), m.group(2).lower()
    return None, None, None


async def _introspect_view(db: asyncpg.Connection, schema: str, code: str) -> dict:
    """Query information_schema to get the column list and source tables of a view.

    Args:
        db: Active database connection.
        schema: PostgreSQL schema name.
        code: View name.

    Returns:
        Dict with 'columns' and 'source_tables', or empty dict on error.
    """
    try:
        col_rows = await rows(
            db,
            """SELECT a.attname AS column_name
               FROM pg_catalog.pg_attribute a
               JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
               JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = $1 AND c.relname = $2
                 AND a.attnum > 0 AND NOT a.attisdropped
               ORDER BY a.attnum""",
            schema, code,
        )
        columns = [r["column_name"] for r in col_rows]

        dep_rows = await rows(
            db,
            """SELECT DISTINCT c.relname AS source_table
               FROM pg_depend d
               JOIN pg_rewrite rw ON rw.oid = d.objid
               JOIN pg_class vc   ON vc.oid = rw.ev_class
               JOIN pg_class c    ON c.oid  = d.refobjid AND c.relkind = 'r'
               JOIN pg_namespace n ON n.oid = vc.relnamespace
               WHERE vc.relname = $1 AND n.nspname = $2""",
            code, schema,
        )
        source_tables = sorted({r["source_table"] for r in dep_rows})
        return {"columns": columns, "source_tables": source_tables}
    except Exception:
        logger.warning("_introspect_view failed for %s.%s", schema, code, exc_info=True)
        return {}


async def _introspect_function(db: asyncpg.Connection, schema: str, code: str) -> dict:
    """Query pg_proc to get the function signature and return type.

    Args:
        db: Active database connection.
        schema: PostgreSQL schema name.
        code: Function name.

    Returns:
        Dict with 'signature', 'parameters', 'returns', or empty dict on error.
    """
    try:
        fn_row = await row(
            db,
            """SELECT pg_get_function_arguments(oid)  AS args,
                      pg_get_function_result(oid)      AS returns
               FROM pg_proc
               WHERE proname = $1
                 AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = $2)
               ORDER BY oid
               LIMIT 1""",
            code, schema,
        )
        if not fn_row:
            return {}

        args_str    = fn_row["args"] or ""
        returns_str = fn_row["returns"] or ""
        signature   = f"{code}({args_str}) RETURNS {returns_str}"

        parameters = []
        for part in args_str.split(","):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if len(tokens) >= 2:
                parameters.append({"name": tokens[0], "type": " ".join(tokens[1:])})
            else:
                parameters.append({"name": part, "type": "unknown"})

        return {"signature": signature, "parameters": parameters, "returns": returns_str}
    except Exception:
        logger.warning("_introspect_function failed for %s.%s", schema, code, exc_info=True)
        return {}


async def _upsert_toolkit_object(
    db: asyncpg.Connection,
    *,
    code: str,
    object_type: str,
    schema_name: str,
    sql_ddl: str,
    user: str,
    ai_meta: dict,
    introspected_meta: dict,
) -> dict | None:
    """Upsert a row in toolkit_objects with merged metadata.

    AI-provided metadata is the base; introspected values override
    columns/signature to reflect actual DB state after execution.

    Args:
        db: Active database connection.
        code: Object code.
        object_type: 'view' or 'function'.
        schema_name: PostgreSQL schema.
        sql_ddl: The DDL SQL that was executed.
        user: Email of the acting admin.
        ai_meta: Metadata from the AI generate-sql response.
        introspected_meta: Metadata from DB introspection.

    Returns:
        Saved toolkit_objects dict, or None on error.
    """
    merged_meta = {**ai_meta, **introspected_meta} if introspected_meta else ai_meta or None

    try:
        saved = await row(
            db,
            """INSERT INTO toolkit_objects
                   (code, object_type, schema_name, label, description,
                    sql, metadata_json, is_active, sort_order, inserted_by, modified_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)
               ON CONFLICT (schema_name, code, object_type) DO UPDATE
               SET sql           = EXCLUDED.sql,
                   label         = EXCLUDED.label,
                   description   = EXCLUDED.description,
                   metadata_json = EXCLUDED.metadata_json,
                   modified_by   = EXCLUDED.modified_by,
                   modified_at   = now()
               RETURNING *""",
            code,
            object_type,
            schema_name,
            ai_meta.get("label") or code,
            ai_meta.get("description") or None,
            sql_ddl,
            merged_meta,
            True,
            0,
            user,
        )
        return dict(saved) if saved else None
    except Exception:
        logger.warning("_upsert_toolkit_object failed for %s", code, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def _check_blocked(sql: str) -> str | None:
    """Return the blocked pattern if found, or None if SQL is safe.

    Args:
        sql: Lowercased SQL string to inspect.

    Returns:
        The matching blocked pattern string, or None.
    """
    sql_lower = sql.lower()
    for blocked in SQL_BLOCKED_PATTERNS:
        if blocked in sql_lower:
            return blocked
    return None


async def validate_sql(db: asyncpg.Connection, sql: str) -> dict:
    """Validate SQL using PostgreSQL's own parser in a rolled-back transaction.

    No changes are committed.  This is the strongest possible validation:
    PG checks syntax, object references, type compatibility, and function bodies.

    Args:
        db: Active database connection.
        sql: SQL string to validate.

    Returns:
        Dict with 'valid' (bool) and 'message' (str).

    Raises:
        ValueError: If sql is empty.
    """
    sql_text = (sql or "").strip()
    if not sql_text:
        raise ValueError("sql is required")

    blocked = _check_blocked(sql_text)
    if blocked:
        return {"valid": False, "message": f"Statement is not allowed: '{blocked}'"}

    try:
        async with db.transaction():
            await db.execute(sql_text)
            raise _RollbackSentinel()
    except _RollbackSentinel:
        return {"valid": True, "message": "SQL parsed and validated by PostgreSQL — no errors found"}
    except Exception as exc:
        return {"valid": False, "message": str(exc)}


async def execute_sql(
    db: asyncpg.Connection,
    sql: str,
    table_code: str,
    object_meta: dict,
    user: str,
) -> dict:
    """Execute SQL via the console.

    For SELECT statements: returns rows (capped at SQL_MAX_ROWS).
    For CREATE VIEW / FUNCTION: executes and auto-catalogs to toolkit_objects.
    For other DDL/DML: executes and logs.

    Args:
        db: Active database connection.
        sql: SQL to execute.
        table_code: Table code for the DDL log (defaults to '__console__').
        object_meta: Optional AI metadata for cataloguing (label, description, etc.).
        user: Email of the acting admin.

    Returns:
        Dict with 'status', 'rows', 'count', and optional 'saved_object'.

    Raises:
        ValueError: If sql is empty or contains a blocked pattern.
    """
    sql_text   = (sql or "").strip()
    table_code = (table_code or "__console__").strip() or "__console__"

    if not sql_text:
        raise ValueError("sql is required")

    blocked = _check_blocked(sql_text)
    if blocked:
        raise ValueError(f"Statement is not allowed: '{blocked}'")

    is_select = sql_text.lower().lstrip().startswith("select")

    if is_select:
        result = await rows(db, sql_text + f" LIMIT {SQL_MAX_ROWS}")
        await execute(
            db,
            "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, executed_by) "
            "VALUES ($1,'execute_sql',$2,TRUE,$3)",
            table_code, sql_text, user,
        )
        return {"rows": result, "count": len(result), "truncated": len(result) == SQL_MAX_ROWS}

    # Non-SELECT (DDL or DML)
    try:
        status = await db.execute(sql_text)
        await execute(
            db,
            "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, executed_by) "
            "VALUES ($1,'execute_sql',$2,TRUE,$3)",
            table_code, sql_text, user,
        )
    except Exception as exc:
        await execute(
            db,
            "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, error_msg, executed_by) "
            "VALUES ($1,'execute_sql',$2,FALSE,$3,$4)",
            table_code, sql_text, str(exc), user,
        )
        raise

    obj_type, schema_name, obj_code = _detect_ddl_object(sql_text)
    saved_object = None

    if obj_type and obj_code:
        if obj_type == "view":
            introspected = await _introspect_view(db, schema_name, obj_code)
        else:
            introspected = await _introspect_function(db, schema_name, obj_code)

        saved_object = await _upsert_toolkit_object(
            db,
            code=obj_code,
            object_type=obj_type,
            schema_name=schema_name,
            sql_ddl=sql_text,
            user=user,
            ai_meta=object_meta,
            introspected_meta=introspected,
        )

    return {
        "status":       status,
        "rows":         [],
        "count":        0,
        "saved_object": saved_object,
    }


async def get_sql_history(
    db: asyncpg.Connection,
    table_code: str | None = None,
    operation: str | None = None,
    limit: int = 50,
) -> list:
    """Return DDL log entries, optionally filtered by table_code and operation.

    Args:
        db: Active database connection.
        table_code: Optional table code filter.
        operation: Optional operation label filter.
        limit: Maximum rows to return.

    Returns:
        List of toolkit_ddl_log dicts ordered by executed_at descending.
    """
    return await rows(
        db,
        """SELECT * FROM toolkit_ddl_log
           WHERE ($1::text IS NULL OR table_code=$1)
             AND ($2::text IS NULL OR operation=$2)
           ORDER BY executed_at DESC LIMIT $3""",
        table_code, operation, limit,
    )
