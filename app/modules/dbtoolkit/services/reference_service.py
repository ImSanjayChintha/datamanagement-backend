"""
Module: toolkit.services.reference_service
Purpose: Business logic for toolkit field reference management.
         Reference config lives directly in toolkit_fields as plain columns:
         ref_table_code, store_field, display_field, ref_type.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute
from app.modules.dbtoolkit.services.activity_service import log_activity, REFERENCE_UPDATED

logger = logging.getLogger(__name__)


async def get_reference(
    db: asyncpg.Connection,
    field_id: int,
) -> dict | None:
    """Return the reference config for a field, or None if not set."""
    field = await row(
        db,
        """SELECT f.code, f.ref_table_code, f.store_field, f.display_field, f.ref_type,
                  t.id AS ref_table_id, t.label AS ref_table_label
           FROM toolkit_fields f
           LEFT JOIN toolkit_tables t ON t.code = f.ref_table_code
           WHERE f.id = $1""",
        field_id,
    )
    if not field or not field["ref_table_code"]:
        return None
    return dict(field)


async def upsert_reference(db: asyncpg.Connection, data: dict) -> dict:
    """Write reference config into toolkit_fields columns.

    Accepts ref_table_code directly, or ref_table_id (resolved to code server-side).
    """
    field_id = data.get("field_id")
    if not field_id:
        raise ValueError("field_id is required")

    ref_table_code = data.get("ref_table_code")
    if not ref_table_code:
        ref_table_id = data.get("ref_table_id")
        if ref_table_id:
            ref_row = await row(db, "SELECT code FROM toolkit_tables WHERE id=$1", ref_table_id)
            if ref_row:
                ref_table_code = ref_row["code"]

    if not ref_table_code:
        raise ValueError("ref_table_code (or ref_table_id) is required")

    field = await row(db, "SELECT code, field_type FROM toolkit_fields WHERE id=$1", field_id)
    if not field:
        raise ValueError("Field not found")
    if field["field_type"] not in ("select", "multiselect"):
        raise ValueError("References only apply to select / multiselect fields")

    store_field   = data.get("store_field")   or "code"
    display_field = data.get("display_field") or "label"
    ref_type      = data.get("ref_type")      or "single"

    await execute(
        db,
        """UPDATE toolkit.toolkit_fields
           SET ref_table_code=$1, store_field=$2, display_field=$3, ref_type=$4
           WHERE id=$5""",
        ref_table_code, store_field, display_field, ref_type, field_id,
    )

    await log_activity(
        db, REFERENCE_UPDATED, "reference", data.get("performed_by"),
        entity_id=field_id, entity_code=field["code"],
        table_code=ref_table_code,
        detail={"ref_table_code": ref_table_code, "store_field": store_field, "display_field": display_field},
    )
    return {
        "field_id":       field_id,
        "ref_table_code": ref_table_code,
        "store_field":    store_field,
        "display_field":  display_field,
        "ref_type":       ref_type,
    }


async def delete_reference(db: asyncpg.Connection, field_id: int) -> bool:
    """Clear reference columns on a field."""
    await execute(
        db,
        """UPDATE toolkit.toolkit_fields
           SET ref_table_code=NULL, store_field=NULL, display_field=NULL, ref_type=NULL
           WHERE id=$1""",
        field_id,
    )
    return True


async def get_reference_options(
    db: asyncpg.Connection,
    field_id: int,
    search: str | None = None,
    limit: int = 100,
) -> list:
    """Fetch dropdown options for a select/multiselect field.

    Reads ref config from toolkit_fields columns, then queries the referenced
    table's view directly.
    """
    field = await row(
        db,
        "SELECT ref_table_code, store_field, display_field FROM toolkit_fields WHERE id=$1",
        field_id,
    )
    if not field or not field["ref_table_code"]:
        raise ValueError("No reference configured for this field")

    view    = f"v_{field['ref_table_code']}"
    store   = field["store_field"]   or "code"
    display = field["display_field"] or "label"

    where_parts = ["is_active = TRUE"]
    params: list = []

    if search:
        params.append(f"%{search}%")
        where_parts.append(f"({store} ILIKE $1 OR {display} ILIKE $1)")

    params.append(limit)
    where_sql = " AND ".join(where_parts)
    sql = (
        f"SELECT {store} AS value, {display} AS label "
        f"FROM {view} WHERE {where_sql} "
        f"ORDER BY sort_order, code LIMIT ${len(params)}"
    )
    return await rows(db, sql, *params)
