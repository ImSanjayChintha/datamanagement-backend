"""
Module: toolkit.services.object_service
Purpose: Business logic for toolkit_objects — AI-generated or manually created
         views, functions, and triggers catalogued in toolkit_objects.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute
from app.modules.dbtoolkit.core.constants import DEFAULT_SCHEMA, OBJECT_TYPES
from app.modules.dbtoolkit.db import exec_ddl, conn_set_user

logger = logging.getLogger(__name__)


async def drop_object(db: asyncpg.Connection, obj: dict, user: str) -> None:
    """Drop the DB object referenced by the catalog record.

    Views are dropped with CASCADE.  Functions are enumerated via pg_proc and
    each overload is dropped individually.  Triggers must be handled by the
    caller since they are attached to a specific table.

    Args:
        db: Active database connection.
        obj: toolkit_objects row dict with 'code', 'object_type', 'schema_name'.
        user: Email of the acting admin (reserved for future audit use).
    """
    schema = (obj.get("schema_name") or DEFAULT_SCHEMA).lower()
    code   = obj["code"]
    otype  = obj["object_type"]

    if otype == "view":
        await db.execute(f'DROP VIEW IF EXISTS "{schema}"."{code}" CASCADE')

    elif otype == "function":
        await db.execute(
            """DO $$ DECLARE r RECORD; BEGIN
               FOR r IN
                 SELECT oid::regprocedure::text AS sig FROM pg_proc
                 WHERE proname = $1
                   AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = $2)
               LOOP
                 EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE';
               END LOOP;
             END $$""",
            code, schema,
        )
    # triggers attached to a table — not dropped here


async def list_objects(
    db: asyncpg.Connection,
    object_type: str | None = None,
) -> list:
    """Return all active toolkit objects, optionally filtered by type.

    Args:
        db: Active database connection.
        object_type: Optional type filter ('view', 'function', 'trigger').

    Returns:
        List of toolkit_objects dicts ordered by object_type and code.
    """
    if object_type:
        return await rows(
            db,
            "SELECT * FROM toolkit_objects WHERE is_active=true AND object_type=$1 ORDER BY code",
            object_type,
        )
    return await rows(
        db,
        "SELECT * FROM toolkit_objects WHERE is_active=true ORDER BY object_type, code",
    )


async def get_object(
    db: asyncpg.Connection,
    id: int | None = None,
    code: str | None = None,
) -> dict:
    """Fetch a single toolkit object by id or code.

    Args:
        db: Active database connection.
        id: Primary key (preferred).
        code: Object code (fallback).

    Returns:
        toolkit_objects dict.

    Raises:
        ValueError: If neither id nor code is given, or the object is not found.
    """
    if id is not None:
        obj = await row(db, "SELECT * FROM toolkit_objects WHERE id=$1", id)
    elif code is not None:
        obj = await row(db, "SELECT * FROM toolkit_objects WHERE code=$1", code)
    else:
        raise ValueError("id or code required")
    if not obj:
        raise ValueError("Object not found")
    return obj


async def create_object(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    """Create a new toolkit object, executing its DDL immediately.

    Args:
        db: Active database connection.
        data: Dict with code, object_type, sql, and optional label/description/
              schema_name/metadata_json/sort_order.
        user: Email of the acting admin.

    Returns:
        Created toolkit_objects dict.

    Raises:
        ValueError: If required fields are missing or object_type is invalid.
    """
    code        = (data.get("code") or "").strip()
    object_type = (data.get("object_type") or "").strip()
    sql_ddl     = (data.get("sql") or "").strip()

    if not code:
        raise ValueError("code is required")
    if not object_type:
        raise ValueError("object_type is required")
    if object_type not in OBJECT_TYPES:
        raise ValueError(f"object_type must be one of {sorted(OBJECT_TYPES)}")
    if not sql_ddl:
        raise ValueError("sql is required")

    await conn_set_user(db, user)
    await exec_ddl(db, sql_ddl, code, f"create_{object_type}", user)

    return await row(
        db,
        """INSERT INTO toolkit_objects
               (code, object_type, schema_name, label, description,
                sql, metadata_json, is_active, sort_order, inserted_by, modified_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)
           ON CONFLICT (schema_name, code, object_type) DO UPDATE
           SET label       = EXCLUDED.label,
               description = EXCLUDED.description,
               sql         = EXCLUDED.sql,
               metadata_json = EXCLUDED.metadata_json,
               is_active   = EXCLUDED.is_active,
               modified_by = EXCLUDED.modified_by,
               modified_at = now()
           RETURNING *""",
        code,
        object_type,
        (data.get("schema_name") or DEFAULT_SCHEMA),
        (data.get("label") or code),
        data.get("description") or None,
        sql_ddl,
        data.get("metadata_json") or None,
        True,
        int(data.get("sort_order", 0)),
        user,
    )


async def update_object(
    db: asyncpg.Connection,
    object_id: int,
    data: dict,
    user: str,
) -> dict:
    """Update a toolkit object and re-execute its DDL.

    Args:
        db: Active database connection.
        object_id: Primary key of the object.
        data: Dict with updated fields (sql, label, description, etc.).
        user: Email of the acting admin.

    Returns:
        Updated toolkit_objects dict.

    Raises:
        ValueError: If the object is not found.
    """
    obj = await row(db, "SELECT * FROM toolkit_objects WHERE id=$1", object_id)
    if not obj:
        raise ValueError("Object not found")

    sql_ddl = (data.get("sql") or obj["sql"]).strip()
    await conn_set_user(db, user)
    await exec_ddl(db, sql_ddl, obj["code"], f"create_{obj['object_type']}", user)

    return await row(
        db,
        """UPDATE toolkit_objects
           SET label         = $1,
               description   = $2,
               sql           = $3,
               metadata_json = $4,
               is_active     = $5,
               modified_by   = $6,
               modified_at   = now()
           WHERE id = $7 RETURNING *""",
        data.get("label", obj["label"]),
        data.get("description", obj["description"]),
        sql_ddl,
        data.get("metadata_json", obj["metadata_json"]),
        bool(data.get("is_active", obj["is_active"])),
        user,
        object_id,
    )


async def delete_object(
    db: asyncpg.Connection,
    object_id: int,
    user: str,
) -> dict:
    """Delete a toolkit object and drop it from the database.

    Args:
        db: Active database connection.
        object_id: Primary key of the object.
        user: Email of the acting admin.

    Returns:
        Dict with 'deleted', 'code', and 'object_type' keys.

    Raises:
        ValueError: If the object is not found.
    """
    obj = await row(db, "SELECT * FROM toolkit_objects WHERE id=$1", object_id)
    if not obj:
        raise ValueError("Object not found")

    await conn_set_user(db, user)
    await drop_object(db, obj, user)
    await execute(db, "DELETE FROM toolkit_objects WHERE id=$1", object_id)
    return {"deleted": True, "code": obj["code"], "object_type": obj["object_type"]}


async def reapply_object(
    db: asyncpg.Connection,
    object_id: int,
    user: str,
) -> dict:
    """Re-execute the stored SQL for an object.

    Useful after a DB restore or migration where the object was dropped.

    Args:
        db: Active database connection.
        object_id: Primary key of the object.
        user: Email of the acting admin.

    Returns:
        Dict with 'reapplied' and 'code' keys.

    Raises:
        ValueError: If the object is not found.
    """
    obj = await row(db, "SELECT * FROM toolkit_objects WHERE id=$1", object_id)
    if not obj:
        raise ValueError("Object not found")

    await conn_set_user(db, user)
    await exec_ddl(db, obj["sql"], obj["code"], f"create_{obj['object_type']}", user)
    return {"reapplied": True, "code": obj["code"]}
