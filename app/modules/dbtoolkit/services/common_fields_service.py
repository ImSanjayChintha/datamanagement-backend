"""
Module: dbtoolkit.services.common_fields_service
Purpose: CRUD for toolkit.common_fields.

common_fields is the toolkit-owned catalog of default fields (id, code, name,
sort_order, is_active, audit cols) that are auto-populated into every new table
built in any schema.  Field-label translations happen in the table builder's
TranslateTab using the company's configured languages.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute, val

logger = logging.getLogger(__name__)

_TABLE = "toolkit.common_fields"
_VIEW  = "toolkit.v_common_fields"

VALID_DATA_TYPES = {
    "text", "number", "integer", "boolean", "date", "datetime",
    "select", "multiselect", "json", "url", "email", "phone", "color",
}
VALID_FIELD_ROLES = {"user", "log"}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def list_common_fields(
    db: asyncpg.Connection,
    search: str | None = None,
    is_active: bool | None = True,
    field_role: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    return await rows(
        db,
        f"""SELECT * FROM {_VIEW}
            WHERE ($1::text IS NULL OR field_name ILIKE '%'||$1||'%' OR code ILIKE '%'||$1||'%')
              AND ($2::boolean IS NULL OR is_active = $2)
              AND ($3::text IS NULL OR field_role = $3)
            ORDER BY sort_order, code
            LIMIT $4 OFFSET $5""",
        search, is_active, field_role, limit, offset,
    )


async def get_common_field(db: asyncpg.Connection, id: int) -> dict:
    result = await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)
    if not result:
        raise ValueError("Common field not found")
    return result



# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

async def create_common_field(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    code       = (data.get("code") or "").strip()
    field_name = (data.get("field_name") or "").strip()
    data_type  = (data.get("data_type") or "").strip().lower()
    field_role = (data.get("field_role") or "user").strip().lower()

    if not code:
        raise ValueError("code is required")
    if not field_name:
        raise ValueError("field_name is required")
    if data_type not in VALID_DATA_TYPES:
        raise ValueError(f"data_type must be one of: {', '.join(sorted(VALID_DATA_TYPES))}")
    if field_role not in VALID_FIELD_ROLES:
        raise ValueError("field_role must be 'user' or 'log'")
    if await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", code):
        raise ValueError(f"Common field '{code}' already exists")

    new_id = await val(
        db,
        f"""INSERT INTO {_TABLE}
                (code, sort_order, field_name, data_type, field_role,
                 show_translation, default_value, true_label, false_label,
                 is_active, inserted_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id""",
        code,
        int(data.get("sort_order", 0)),
        field_name,
        data_type,
        field_role,
        bool(data.get("show_translation", True)),
        data.get("default_value") or None,
        data.get("true_label")    or None,
        data.get("false_label")   or None,
        bool(data.get("is_active", True)),
        user,
    )

    return await get_common_field(db, new_id)


async def update_common_field(
    db: asyncpg.Connection,
    id: int,
    data: dict,
    user: str,
) -> dict:
    existing = await get_common_field(db, id)

    data_type  = (data.get("data_type") or existing["data_type"]).strip().lower()
    field_role = (data.get("field_role") or existing["field_role"]).strip().lower()

    if data_type not in VALID_DATA_TYPES:
        raise ValueError(f"data_type must be one of: {', '.join(sorted(VALID_DATA_TYPES))}")
    if field_role not in VALID_FIELD_ROLES:
        raise ValueError("field_role must be 'user' or 'log'")

    await execute(
        db,
        f"""UPDATE {_TABLE}
            SET code=$1, field_name=$2, data_type=$3, field_role=$4,
                show_translation=$5, is_active=$6, sort_order=$7,
                default_value=$8, true_label=$9, false_label=$10,
                modified_at=NOW(), modified_by=$11
            WHERE id=$12""",
        data.get("code",             existing["code"]),
        data.get("field_name",       existing["field_name"]),
        data_type,
        field_role,
        bool(data.get("show_translation", existing.get("show_translation", True))),
        bool(data.get("is_active",       existing["is_active"])),
        int(data.get("sort_order",       existing["sort_order"])),
        data.get("default_value", existing.get("default_value")) or None,
        data.get("true_label",    existing.get("true_label"))    or None,
        data.get("false_label",   existing.get("false_label"))   or None,
        user,
        id,
    )

    return await get_common_field(db, id)


async def delete_common_field(
    db: asyncpg.Connection,
    id: int,
    soft: bool = True,
) -> dict:
    await get_common_field(db, id)
    if soft:
        await execute(
            db,
            f"UPDATE {_TABLE} SET is_active=FALSE, modified_at=NOW() WHERE id=$1",
            id,
        )
    else:
        await execute(db, f"DELETE FROM {_TABLE} WHERE id=$1", id)
    return {"deleted": True, "soft": soft, "id": id}
