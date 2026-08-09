"""
Module: toolkit.services.field_service
Purpose: Business logic for toolkit field management — create, update, delete,
         reorder, and view reload after field changes.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute
from app.modules.dbtoolkit.core.ddl import (
    build_add_column,
    build_drop_column,
    build_rename_column,
    build_create_view,
    build_list_function,
    build_get_function,
    pg_type,
    safe_name,
)
from app.modules.dbtoolkit.db import exec_ddl, actual_cols
from app.modules.dbtoolkit.services.activity_service import (
    log_activity,
    FIELD_CREATED, FIELD_UPDATED, FIELD_RENAMED, FIELD_DELETED,
)

logger = logging.getLogger(__name__)

# ── api_fields field_type mapping ─────────────────────────────────────────────
# Toolkit uses a richer set of field types; api_fields has a constrained CHECK.

_TOOLKIT_TO_API_TYPE: dict[str, str] = {
    "text":         "text",
    "textarea":     "text",
    "url":          "text",
    "email":        "text",
    "uuid":         "text",
    "number":       "number",
    "integer":      "number",
    "decimal":      "decimal",
    "boolean":      "boolean",
    "date":         "date",
    "timestamp":    "timestamp",
    "json":         "json",
    "jsonb":        "json",
    "i18n_text":    "i18n_text",
    "i18n_richtext":"i18n_richtext",
    "select":       "select",
    "multiselect":  "select",
    "reference":    "reference",
    "daterange":    "text",
    "text_array":   "text",
}

# json/richtext fields are too heavy to include in list views by default
_NOT_IN_LIST = {"json", "jsonb", "i18n_richtext"}


def _api_field_type(ftype: str) -> str:
    return _TOOLKIT_TO_API_TYPE.get(ftype, "text")


async def sync_field_to_api(
    db:     asyncpg.Connection,
    table:  dict,
    field:  dict,
    delete: bool = False,
) -> None:
    """Keep toolkit.api_fields in sync when a field is created, updated, or deleted.

    Silently skips tables that have no registered toolkit.api_objects entry.
    Also invalidates the engine registry cache so the change is picked up immediately.
    """
    from app.modules.api_bridge.engine.registry import registry

    schema_name = table.get("schema_name") or "public"
    table_code  = table.get("code") or ""
    object_code = f"{schema_name}.{table_code}"

    # Only sync tables that have a registered API object
    api_obj = await row(
        db,
        "SELECT code FROM toolkit.api_objects WHERE code=$1 AND is_active=TRUE",
        object_code,
    )
    if not api_obj:
        return

    field_code = f"{object_code}.{field['code']}"

    if delete:
        await execute(db, "DELETE FROM toolkit.api_fields WHERE code=$1", field_code)
        await registry.invalidate(object_code)
        return

    ftype        = _api_field_type(field.get("field_type") or "text")
    label        = (field.get("label") or field.get("code") or "").strip()
    desc         = (field.get("description") or "").strip()
    is_system    = bool(field.get("is_system", False))
    in_list      = (field.get("field_type") or "text") not in _NOT_IN_LIST

    await execute(
        db,
        """INSERT INTO toolkit.api_fields
               (code, object_code, field_name, field_type, "name", description,
                is_required, is_unique, is_readonly, editable_on_update,
                in_list, in_form, sort_order, is_active)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,TRUE,$10,TRUE,$11,TRUE)
           ON CONFLICT (code) DO UPDATE
               SET field_type         = EXCLUDED.field_type,
                   "name"             = EXCLUDED."name",
                   description        = EXCLUDED.description,
                   is_required        = EXCLUDED.is_required,
                   is_unique          = EXCLUDED.is_unique,
                   is_readonly        = EXCLUDED.is_readonly,
                   in_list            = EXCLUDED.in_list,
                   sort_order         = EXCLUDED.sort_order,
                   is_active          = TRUE,
                   modified_at        = NOW()""",
        field_code,
        object_code,
        field["code"],
        ftype,
        {"en": label},
        {"en": desc} if desc else None,
        bool(field.get("is_required", False)),
        bool(field.get("is_unique", False)),
        is_system,           # system fields are readonly in the API
        in_list,
        int(field.get("sort_order", 999)),
    )

    await registry.invalidate(object_code)


async def reload_table_views(
    db: asyncpg.Connection,
    table_id: int,
    user: str,
) -> None:
    """Regenerate the view and list/get functions for a table.

    Called after any field change that could affect the view definition.
    Fetches ref-field metadata so that multilingual display fields on
    referenced tables are rendered with the correct COALESCE expression.

    Args:
        db: Active database connection.
        table_id: Primary key in toolkit_tables.
        user: Email of the acting admin (for DDL log).
    """
    from app.modules.dbtoolkit.services.table_service import fetch_ref_fields

    tbl      = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    flds_raw = await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order",
        table_id,
    )
    flds    = [dict(f) for f in flds_raw]
    cols              = await actual_cols(db, dict(tbl))
    ref_map, schema_map = await fetch_ref_fields(db, flds)

    # Catch-up: add any physical columns that are in the catalog but missing from the DB.
    # This handles fields created before the multilingual-column fix was applied.
    tbl_dict = dict(tbl)
    for f in flds:
        if pg_type(f) is None or f.get("is_system") or f.get("field_type") == "daterange":
            continue
        fc = safe_name(f["code"])
        if fc not in cols:
            try:
                await exec_ddl(db, build_add_column(tbl_dict, f), tbl_dict["code"], "add_field", user)
                cols.add(fc)
                logger.info("[reload] added missing column %s.%s", tbl_dict["code"], fc)
            except Exception as exc:
                logger.warning("[reload] could not add column %s.%s: %s", tbl_dict["code"], fc, exc)

    for op, sql in [
        ("create_view",     build_create_view(tbl, flds, cols, ref_fields_map=ref_map, ref_schema_map=schema_map)),
        ("create_function", build_list_function(tbl, flds, cols)),
        ("create_function", build_get_function(tbl, flds, cols)),
    ]:
        try:
            await exec_ddl(db, sql, tbl["code"], op, user)
        except Exception as exc:
            logger.error("%s failed for table %s: %s", op, tbl["code"], exc)
            raise ValueError(f"'{op}' failed for table '{tbl['code']}': {exc}") from exc


async def list_fields(
    db: asyncpg.Connection,
    table_id: int | None = None,
    table_code: str | None = None,
) -> list:
    """Return ordered fields for a table.

    Args:
        db: Active database connection.
        table_id: Table primary key (preferred).
        table_code: Table code (fallback).

    Returns:
        List of field dicts from v_toolkit_fields.

    Raises:
        ValueError: If neither table_id nor table_code is provided.
    """
    if table_id is not None:
        return await rows(
            db,
            "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order",
            table_id,
        )
    elif table_code is not None:
        return await rows(
            db,
            "SELECT * FROM toolkit.v_toolkit_fields WHERE table_code=$1 ORDER BY sort_order",
            table_code,
        )
    else:
        raise ValueError("table_id or table_code required")


async def create_field(
    db: asyncpg.Connection,
    field_data: dict,
    user: str,
) -> dict:
    """Add a new field to a toolkit table.

    Inserts the catalog record, runs ADD COLUMN DDL (for physical types),
    creates the junction table for multiselect, then regenerates the view.

    Args:
        db: Active database connection.
        field_data: Dict with table_id, code, label, field_type, etc.
        user: Email of the acting admin.

    Returns:
        Created field dict from toolkit_fields.

    Raises:
        ValueError: If required fields are missing, the table is not found,
            the field already exists, or the field_type is unknown.
    """
    from app.modules.dbtoolkit.core.constants import FIELD_TYPES_ALL

    table_id = field_data.get("table_id")
    code     = (field_data.get("code") or "").strip().lower()
    label    = (field_data.get("label") or "").strip()
    ftype    = (field_data.get("field_type") or "text").strip()
    desc     = (field_data.get("description") or "").strip()

    if not table_id or not code or not label:
        raise ValueError("table_id, code, label are required")
    if not desc:
        raise ValueError("description is required on every field")
    if ftype not in FIELD_TYPES_ALL:
        raise ValueError(f"Unknown field_type '{ftype}'")

    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        raise ValueError("Table not found")

    if await row(db, "SELECT id FROM toolkit_fields WHERE table_id=$1 AND code=$2", table_id, code):
        raise ValueError(f"Field '{code}' already exists on this table")

    field = await row(
        db,
        """INSERT INTO toolkit_fields
               (table_id, code, label, description, field_type, is_required,
                is_unique, is_multilingual, is_system, config, default_value, sort_order)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,FALSE,$9,$10,$11) RETURNING *""",
        table_id, code, label, desc, ftype,
        bool(field_data.get("is_required", False)),
        bool(field_data.get("is_unique", False)),
        bool(field_data.get("is_multilingual", False)),
        field_data.get("config") or {},
        field_data.get("default_value"),
        int(field_data.get("sort_order", 999)),
    )

    if pg_type(field) is not None:
        try:
            await exec_ddl(db, build_add_column(tbl, field), tbl["code"], "add_field", user)
        except Exception as exc:
            logger.error("add_field DDL failed for %s.%s: %s", tbl["code"], code, exc)
            raise ValueError(f"Could not add column '{code}' to '{tbl['code']}': {exc}") from exc

    if ftype == "multiselect":
        jt  = f"{safe_name(tbl['code'])}_{safe_name(code)}_links"
        sql = f"""CREATE TABLE IF NOT EXISTS {jt} (
    id          BIGSERIAL PRIMARY KEY,
    source_id   UUID NOT NULL REFERENCES {safe_name(tbl['code'])}(id) ON DELETE CASCADE,
    target_value TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, target_value)
);"""
        try:
            await exec_ddl(db, sql, tbl["code"], "add_field", user)
        except Exception as exc:
            logger.error("create junction table %s failed: %s", jt, exc)
            raise ValueError(f"Could not create junction table '{jt}': {exc}") from exc

    try:
        await reload_table_views(db, table_id, user)
    except Exception as exc:
        logger.error("reload_table_views failed after create_field %s.%s: %s", tbl["code"], code, exc)
        raise
    await sync_field_to_api(db, dict(tbl), dict(field))
    await log_activity(
        db, FIELD_CREATED, "field", user,
        entity_id=field["id"], entity_code=code,
        schema_name=tbl.get("schema_name"), table_code=tbl["code"],
        detail={"field_type": ftype, "is_multilingual": bool(field.get("is_multilingual"))},
    )
    return field


async def update_field(
    db: asyncpg.Connection,
    field_id: int,
    field_data: dict,
    user: str,
) -> dict:
    """Update a field's metadata.

    Handles column rename DDL, reference config updates, and view regeneration.

    Args:
        db: Active database connection.
        field_id: Primary key of the field in toolkit_fields.
        field_data: Dict of updated field properties.
        user: Email of the acting admin.

    Returns:
        Updated field dict.

    Raises:
        ValueError: If the field is not found or is a system field.
    """
    from app.modules.dbtoolkit.services.reference_service import upsert_reference

    existing = await row(db, "SELECT * FROM toolkit_fields WHERE id=$1", field_id)
    if not existing:
        raise ValueError("Field not found")
    if existing["is_system"]:
        raise ValueError("System fields cannot be modified")

    tbl      = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", existing["table_id"])
    new_code = (field_data.get("code") or existing["code"]).strip().lower()

    if new_code != existing["code"] and pg_type(existing) is not None:
        # Only rename if the old column exists and the new name is not already taken
        cols = await actual_cols(db, dict(tbl))
        if safe_name(existing["code"]) in cols and safe_name(new_code) not in cols:
            try:
                sql = build_rename_column(tbl, existing["code"], new_code)
                await exec_ddl(db, sql, tbl["code"], "rename_field", user)
            except Exception as exc:
                logger.error("rename_field failed %s -> %s: %s", existing["code"], new_code, exc)
                raise ValueError(
                    f"Could not rename column '{existing['code']}' to '{new_code}': {exc}"
                ) from exc

    # If new_code conflicts with a sibling field, fall back to the existing code to avoid
    # a UniqueViolationError. This handles stale catalog rows left by a prior silent rename failure.
    conflict = await row(
        db,
        "SELECT id FROM toolkit_fields WHERE table_id=$1 AND code=$2 AND id<>$3",
        existing["table_id"], new_code, field_id,
    )
    if conflict:
        new_code = existing["code"]

    updated = await row(
        db,
        """UPDATE toolkit_fields
           SET code=$1, label=$2, description=$3, is_required=$4,
               is_unique=$5, is_multilingual=$6, config=$7, default_value=$8,
               sort_order=$9, modified_at=now()
           WHERE id=$10 RETURNING *""",
        new_code,
        field_data.get("label", existing["label"]),
        field_data.get("description", existing["description"]),
        bool(field_data.get("is_required", existing["is_required"])),
        bool(field_data.get("is_unique", existing["is_unique"])),
        bool(field_data.get("is_multilingual", existing["is_multilingual"])),
        field_data.get("config", existing["config"]) or {},
        field_data.get("default_value", existing["default_value"]),
        int(field_data.get("sort_order", existing["sort_order"])),
        field_id,
    )

    # Update reference config when the user changes which table a select/multiselect field
    # points to. field_data may supply ref_table_code (string) or ref_table_id (int).
    if existing["field_type"] in ("select", "multiselect"):
        ref_table_id   = field_data.get("ref_table_id")
        ref_table_code = field_data.get("ref_table_code")
        if not ref_table_id and ref_table_code:
            ref_row = await row(db, "SELECT id FROM toolkit_tables WHERE code=$1", ref_table_code)
            if ref_row:
                ref_table_id = ref_row["id"]
        if ref_table_id:
            try:
                await upsert_reference(db, {
                    "field_id":          field_id,
                    "ref_table_id":      ref_table_id,
                    "store_field":       field_data.get("store_field", "code"),
                    "display_field":     field_data.get("display_field", "label"),
                    "display_template":  field_data.get("display_template"),
                    "search_fields":     field_data.get("search_fields") or ["code", "label"],
                    "filter_conditions": field_data.get("filter_conditions"),
                    "order_by":          field_data.get("order_by") or "sort_order, code",
                    "ref_type":          field_data.get("ref_type", "single"),
                    "cascade_on_delete": field_data.get("cascade_on_delete", "restrict"),
                })
            except Exception as exc:
                logger.error("upsert_reference failed for field %s: %s", field_id, exc)
                raise ValueError(f"Could not save reference config for field '{existing['code']}': {exc}") from exc

    try:
        await reload_table_views(db, existing["table_id"], user)
    except Exception as exc:
        logger.error("reload_table_views failed after update_field for field %s: %s", field_id, exc)
        raise
    await sync_field_to_api(db, dict(tbl), dict(updated))

    activity = FIELD_RENAMED if new_code != existing["code"] else FIELD_UPDATED
    await log_activity(
        db, activity, "field", user,
        entity_id=field_id, entity_code=new_code,
        schema_name=tbl.get("schema_name"), table_code=tbl["code"],
        detail={
            "old_code": existing["code"],
            "new_code": new_code,
            "field_type": existing["field_type"],
        } if activity == FIELD_RENAMED else {
            "field_type": existing["field_type"],
        },
    )
    return updated


async def delete_field(
    db: asyncpg.Connection,
    field_id: int,
    user: str,
) -> dict:
    """Delete a field and its physical column (if any).

    Also drops the junction table for multiselect fields.

    Args:
        db: Active database connection.
        field_id: Primary key of the field in toolkit_fields.
        user: Email of the acting admin.

    Returns:
        Dict with 'deleted' key.

    Raises:
        ValueError: If the field is not found or is a system field.
    """
    field = await row(db, "SELECT * FROM toolkit_fields WHERE id=$1", field_id)
    if not field:
        raise ValueError("Field not found")
    if field["is_system"]:
        raise ValueError("System fields cannot be deleted")

    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", field["table_id"])

    if pg_type(field) is not None:
        try:
            await exec_ddl(
                db, build_drop_column(tbl, field["code"]), tbl["code"], "drop_field", user
            )
        except Exception as exc:
            logger.error("drop_field DDL failed for %s.%s: %s", tbl["code"], field["code"], exc)
            raise ValueError(f"Could not drop column '{field['code']}' from '{tbl['code']}': {exc}") from exc

    if field["field_type"] == "multiselect":
        jt = f"{safe_name(tbl['code'])}_{safe_name(field['code'])}_links"
        try:
            await exec_ddl(
                db, f"DROP TABLE IF EXISTS {jt} CASCADE;", tbl["code"], "drop_field", user
            )
        except Exception as exc:
            logger.error("drop junction table %s failed: %s", jt, exc)
            raise ValueError(f"Could not drop junction table '{jt}': {exc}") from exc

    await execute(db, "DELETE FROM toolkit_fields WHERE id=$1", field_id)
    try:
        await reload_table_views(db, field["table_id"], user)
    except Exception as exc:
        logger.error("reload_table_views failed after delete_field %s.%s: %s", tbl["code"], field["code"], exc)
        raise
    await sync_field_to_api(db, dict(tbl), dict(field), delete=True)
    await log_activity(
        db, FIELD_DELETED, "field", user,
        entity_id=field_id, entity_code=field["code"],
        schema_name=tbl.get("schema_name"), table_code=tbl["code"],
        detail={"field_type": field["field_type"]},
    )
    return {"deleted": True}


async def reorder_fields(db: asyncpg.Connection, order: list) -> bool:
    """Update sort_order for a list of fields.

    Args:
        db: Active database connection.
        order: List of dicts with 'id' and 'sort_order' keys.

    Returns:
        True on success.
    """
    for item in order:
        await execute(
            db,
            "UPDATE toolkit_fields SET sort_order=$1 WHERE id=$2",
            int(item["sort_order"]), int(item["id"]),
        )
    return True
