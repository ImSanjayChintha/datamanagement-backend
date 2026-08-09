"""
Module: toolkit.ddl.executor
Purpose: Execute DDL statements against the database, log results to
         toolkit_ddl_log, and manage PostgreSQL session variables used
         by the audit trigger.
"""
import logging
import asyncpg
from app.core.database import execute, rows
from app.modules.dbtoolkit.core.constants import USER_SESSION_VAR, DEFAULT_SCHEMA

logger = logging.getLogger(__name__)


async def conn_set_user(db: asyncpg.Connection, user: str) -> None:
    """Set the current user session variable for the audit trigger.

    The audit trigger (fn_toolkit_set_audit) reads app.current_user to
    populate inserted_by / modified_by columns automatically.

    Args:
        db: Active database connection.
        user: Email or identifier of the acting user.
    """
    safe = user.replace("'", "")
    await db.execute(f"SET LOCAL \"{USER_SESSION_VAR}\" = '{safe}'")


async def exec_ddl(
    db: asyncpg.Connection,
    sql: str,
    table_code: str,
    operation: str,
    user: str | None = None,
    _log_batch: list | None = None,
) -> None:
    """Execute a DDL statement and log the result to toolkit_ddl_log.

    When _log_batch is provided (a list), the log INSERT is deferred — the
    caller accumulates entries and flushes them with flush_ddl_log() in one
    round-trip.  When None, the log INSERT happens immediately (old behaviour).

    Args:
        db: Active database connection.
        sql: The DDL SQL to execute.
        table_code: Table code for the audit log (may be a view/function code).
        operation: Short label for the log (e.g. 'create_table', 'create_view').
        user: Acting admin email — written to the log.
        _log_batch: Optional list to accumulate log entries instead of writing immediately.

    Raises:
        Exception: Re-raises any database error after logging the failure.
    """
    try:
        await db.execute(sql)
        entry = (table_code, operation, sql, True, None, user)
        if _log_batch is not None:
            _log_batch.append(entry)
        else:
            await db.execute(
                "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, error_msg, executed_by) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                *entry,
            )
    except Exception as exc:
        logger.error(
            "exec_ddl FAILED [%s/%s]: %s\n--- SQL ---\n%s\n--- END ---",
            table_code, operation, exc, sql,
        )
        err_entry = (table_code, operation, sql, False, str(exc), user)
        try:
            await db.execute(
                "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, error_msg, executed_by) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                *err_entry,
            )
        except Exception:
            logger.warning("Failed to log DDL error for %s/%s", table_code, operation)
        raise


async def flush_ddl_log(db: asyncpg.Connection, batch: list) -> None:
    """Write a batch of DDL log entries accumulated via exec_ddl(_log_batch=...)."""
    if not batch:
        return
    await db.executemany(
        "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, error_msg, executed_by) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        batch,
    )


async def actual_cols(db: asyncpg.Connection, table: dict) -> set[str]:
    """Return the set of column names that physically exist in the database table.

    Used to guard against referencing columns that were added in a later
    migration but not yet reflected in the catalog.

    Args:
        db: Active database connection.
        table: Table dict with at least 'code' and optionally 'schema_name'.

    Returns:
        Set of column name strings, or empty set if the table does not exist.
    """
    schema = (table.get("schema_name") or DEFAULT_SCHEMA).lower()
    tbl    = table["code"].lower()
    col_rows = await rows(
        db,
        """SELECT a.attname AS column_name
           FROM pg_catalog.pg_attribute a
           JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = $1 AND c.relname = $2
             AND a.attnum > 0 AND NOT a.attisdropped""",
        schema, tbl,
    )
    return {r["column_name"] for r in col_rows}


# Maps PostgreSQL internal type names (udt_name) to standard type strings.
_UDT_TO_PG: dict[str, str] = {
    "text": "text", "varchar": "text", "bpchar": "text",
    "jsonb": "jsonb", "json": "jsonb",
    "int4": "integer", "int8": "bigint", "int2": "smallint",
    "float4": "real", "float8": "double precision",
    "numeric": "numeric",
    "bool": "boolean",
    "date": "date", "time": "time", "timetz": "time",
    "timestamptz": "timestamptz", "timestamp": "timestamp",
    "uuid": "uuid",
    "bigserial": "bigserial", "serial": "integer",
    # Array types — PostgreSQL internal udt_name prefixes arrays with '_'
    "_text": "text[]",
}


async def actual_col_types(db: asyncpg.Connection, table: dict) -> dict[str, str]:
    """Return the physical column types currently in the database.

    Returns {column_name: pg_type_string} for every column in the table, where
    pg_type_string is a normalised value comparable to what pg_type() returns
    (e.g. 'text', 'jsonb', 'integer', 'boolean').  Returns an empty dict when
    the table does not yet exist.

    Args:
        db: Active database connection.
        table: Table dict with at least 'code' and optionally 'schema_name'.
    """
    schema = (table.get("schema_name") or DEFAULT_SCHEMA).lower()
    tbl    = table["code"].lower()
    col_rows = await rows(
        db,
        """SELECT a.attname AS column_name, t.typname AS udt_name
           FROM pg_catalog.pg_attribute a
           JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
           WHERE n.nspname = $1 AND c.relname = $2
             AND a.attnum > 0 AND NOT a.attisdropped""",
        schema, tbl,
    )
    return {r["column_name"]: _UDT_TO_PG.get(r["udt_name"], r["udt_name"]) for r in col_rows}
