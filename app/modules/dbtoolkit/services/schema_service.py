"""
Module: toolkit.services.schema_service
Purpose: Business logic for PostgreSQL schema management — listing existing
         schemas and creating new domain schemas for use by toolkit tables.

Rules enforced here:
  • Schema names must match ^[a-z][a-z0-9_]{0,62}$ (PostgreSQL identifier safe).
  • Reserved names (public, admin, pg_*, information_schema, etc.) cannot be
    created through this service — they are managed by migrations.
  • Creating a schema that already exists is a no-op (idempotent).
  • The search_path in database.py must be updated manually after adding a new
    schema so it is reachable by unqualified names in the application.
"""
import re
import logging

import asyncpg

from app.core.database import rows, row, execute

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')

# Schemas that exist by default or are managed exclusively by migrations.
# Users may not create or alter these through the API.
_RESERVED_SCHEMAS: frozenset[str] = frozenset({
    "toolkit", "public", "admin", "pg_catalog", "pg_toast",
    "information_schema", "pg_temp", "pg_toast_temp",
})


def _validate_schema_name(name: str) -> str:
    """Validate and normalise a schema name.

    Args:
        name: Proposed schema name (will be lowercased).

    Returns:
        Normalised (lowercased, stripped) schema name.

    Raises:
        ValueError: If the name is reserved or does not match the safe pattern.
    """
    name = (name or "").strip().lower()
    if not name:
        raise ValueError("schema name is required")
    if name in _RESERVED_SCHEMAS or name.startswith("pg_"):
        raise ValueError(f"'{name}' is a reserved schema name and cannot be created via the API")
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(
            "schema name must start with a letter, contain only lowercase letters, "
            "digits, and underscores, and be at most 63 characters"
        )
    return name


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

async def list_schemas(db: asyncpg.Connection) -> list[dict]:
    """Return all user-visible schemas with their descriptions.

    Excludes internal PostgreSQL schemas (pg_*, information_schema).
    Returns schemas ordered by name with a flag indicating whether the
    schema is a reserved platform schema or a user-created domain schema.

    Args:
        db: Active database connection.

    Returns:
        List of dicts with keys: name, description, is_reserved, table_count.
    """
    schema_rows = await rows(
        db,
        """
        SELECT
            n.nspname                          AS name,
            obj_description(n.oid, 'pg_namespace') AS description,
            COUNT(c.relname)::INT              AS table_count
        FROM   pg_catalog.pg_namespace n
        LEFT JOIN pg_catalog.pg_class c
               ON c.relnamespace = n.oid AND c.relkind = 'r'
        WHERE  n.nspname NOT LIKE 'pg\\_%'
          AND  n.nspname <> 'information_schema'
        GROUP  BY n.nspname, n.oid
        ORDER  BY n.nspname
        """,
    )
    reserved = _RESERVED_SCHEMAS | {"public"}
    return [
        {
            "name":        r["name"],
            "description": r["description"] or "",
            "is_reserved": r["name"] in reserved,
            "table_count": r["table_count"],
        }
        for r in schema_rows
    ]


async def get_schema(db: asyncpg.Connection, name: str) -> dict:
    """Return details for a single schema.

    Args:
        db: Active database connection.
        name: Schema name (case-insensitive; normalised to lowercase).

    Returns:
        Dict with name, description, is_reserved, table_count, and tables list.

    Raises:
        ValueError: If the schema does not exist.
    """
    name = (name or "").strip().lower()
    schema_row = await row(
        db,
        """
        SELECT
            n.nspname                              AS name,
            obj_description(n.oid, 'pg_namespace') AS description
        FROM   pg_catalog.pg_namespace n
        WHERE  n.nspname = $1
        """,
        name,
    )
    if not schema_row:
        raise ValueError(f"Schema '{name}' does not exist")

    table_rows = await rows(
        db,
        """
        SELECT
            c.relname                                   AS table_name,
            obj_description(c.oid, 'pg_class')          AS description,
            (SELECT COUNT(*) FROM pg_catalog.pg_attribute a
             WHERE  a.attrelid = c.oid AND a.attnum > 0
               AND  NOT a.attisdropped)::INT            AS column_count
        FROM   pg_catalog.pg_class     c
        JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE  n.nspname = $1
          AND  c.relkind = 'r'
        ORDER  BY c.relname
        """,
        name,
    )

    return {
        "name":        schema_row["name"],
        "description": schema_row["description"] or "",
        "is_reserved": name in (_RESERVED_SCHEMAS | {"public"}),
        "table_count": len(table_rows),
        "tables":      [dict(t) for t in table_rows],
    }


async def create_schema(
    db: asyncpg.Connection,
    name: str,
    description: str | None,
    user: str,
) -> dict:
    """Create a new PostgreSQL schema for use by toolkit domain tables.

    Idempotent — returns success if the schema already exists.

    Args:
        db: Active database connection.
        name: Desired schema name (lowercased, validated).
        description: Optional COMMENT ON SCHEMA description.
        user: Email of the acting admin (for audit logging).

    Returns:
        Dict with name, description, created (bool), message.

    Raises:
        ValueError: If the name is reserved or invalid.
    """
    name = _validate_schema_name(name)

    # Check if already exists
    existing = await row(
        db,
        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = $1",
        name,
    )
    if existing:
        return {
            "name":        name,
            "description": description or "",
            "created":     False,
            "message":     f"Schema '{name}' already exists — no action taken",
        }

    # Create the schema
    await db.execute(f"CREATE SCHEMA {name}")

    # Attach a description if provided
    if description:
        safe_desc = description.replace("'", "''")
        await db.execute(f"COMMENT ON SCHEMA {name} IS '{safe_desc}'")

    # Audit log
    sql_executed = f"CREATE SCHEMA {name}" + (
        f"; COMMENT ON SCHEMA {name} IS '...'" if description else ""
    )
    await execute(
        db,
        "INSERT INTO toolkit_ddl_log (table_code, operation, sql_executed, success, executed_by) "
        "VALUES ($1,$2,$3,TRUE,$4)",
        f"_schema_{name}", "create_schema", sql_executed, user,
    )

    logger.info("Schema '%s' created by %s", name, user)
    return {
        "name":        name,
        "description": description or "",
        "created":     True,
        "message":     (
            f"Schema '{name}' created. "
            "Remember to add it to the search_path in database.py and "
            "the ALTER DATABASE search_path migration."
        ),
    }
