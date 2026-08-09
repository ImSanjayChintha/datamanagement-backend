"""
Module: toolkit.services.data_service
Purpose: Generic data CRUD for any toolkit-managed table.

All reads go through v_{table_code} (extracts current language from jsonb columns).
Writes go directly to the base table — is_multilingual fields are stored as
{"en": "...", "fr": "..."} jsonb dicts in their physical column.
No separate translations table is used for is_multilingual fields.
"""
import json
import logging

import asyncpg

from app.core.database import row, rows, execute, val
from app.modules.dbtoolkit.core.constants import DEFAULT_SCHEMA, FIELD_TYPES_NO_COLUMN, LANG_SESSION_VAR, USER_SESSION_VAR
from app.modules.dbtoolkit.core.ddl import pg_type, safe_name

logger = logging.getLogger(__name__)

_WRITABLE_SKIP = {"id", "inserted_at", "inserted_by", "modified_at", "modified_by"}


async def load_schema(
    db: asyncpg.Connection,
    table_code: str,
) -> tuple[dict, list[dict]]:
    tbl = await row(
        db,
        "SELECT * FROM toolkit_tables WHERE code=$1 AND is_active=TRUE AND schema_name != 'admin'",
        table_code,
    )
    if not tbl:
        raise ValueError(f"Table '{table_code}' not found")
    flds = await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_code=$1 ORDER BY sort_order",
        table_code,
    )
    return tbl, flds


async def set_request_lang(db: asyncpg.Connection, lang: str | None) -> None:
    lang = (lang or "en").strip().lower()[:5]
    await db.execute(f"SET LOCAL {LANG_SESSION_VAR} = '{lang}'")


async def _set_current_user(db: asyncpg.Connection, user: str) -> None:
    await db.execute(f"SET LOCAL {USER_SESSION_VAR} = '{user.replace(chr(39), '')}'")


def _to_jsonb_str(val_raw: object) -> str:
    """Coerce a value to a JSON string suitable for a jsonb column."""
    if isinstance(val_raw, str):
        try:
            json.loads(val_raw)
            return val_raw  # already valid JSON
        except (ValueError, TypeError):
            return json.dumps({"en": val_raw})
    return json.dumps(val_raw if val_raw is not None else {})


async def list_records(
    db: asyncpg.Connection,
    table_code: str,
    search: str | None = None,
    is_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    lang: str | None = None,
) -> list:
    tbl, _ = await load_schema(db, table_code)
    schema  = tbl.get("schema_name") or DEFAULT_SCHEMA
    tbl_name = safe_name(table_code)
    await set_request_lang(db, lang)
    return await rows(
        db,
        f"""SELECT * FROM {schema}.v_{tbl_name}
            WHERE ($1::text IS NULL OR code ILIKE '%'||$1||'%')
              AND ($2::boolean IS NULL OR is_active = $2)
            ORDER BY sort_order, code
            LIMIT $3 OFFSET $4""",
        search, is_active, limit, offset,
    )


async def get_record(
    db: asyncpg.Connection,
    table_code: str,
    record_id: int,
    lang: str | None = None,
) -> dict:
    """Fetch a single record.

    Returns the view row (current-language strings) plus a '_translations' key
    containing the raw jsonb dicts for all is_multilingual fields so that edit
    forms can populate every language tab.
    """
    tbl, flds = await load_schema(db, table_code)
    schema    = tbl.get("schema_name") or DEFAULT_SCHEMA
    tbl_name  = safe_name(table_code)
    qual      = f"{schema}.{tbl_name}"
    await set_request_lang(db, lang)

    record = await row(db, f"SELECT * FROM {schema}.v_{tbl_name} WHERE id=$1", int(record_id))
    if not record:
        raise ValueError("Record not found")

    # Collect raw jsonb dicts from the base table for every is_multilingual field
    multi_flds = [f for f in flds if f["is_multilingual"]]
    translations: dict[str, dict] = {}

    if multi_flds:
        cols     = ", ".join(f'"{safe_name(f["code"])}"' for f in multi_flds)
        base_row = await row(db, f"SELECT {cols} FROM {qual} WHERE id=$1", int(record_id))
        if base_row:
            for f in multi_flds:
                raw = base_row[safe_name(f["code"])]
                if isinstance(raw, dict):
                    translations[f["code"]] = raw
                elif isinstance(raw, str):
                    try:
                        translations[f["code"]] = json.loads(raw)
                    except (ValueError, TypeError):
                        translations[f["code"]] = {"en": raw}
                else:
                    translations[f["code"]] = {}

    # has_label: if the label column is a jsonb column that is not a user-defined
    # is_multilingual field, fetch it separately so the edit form can populate it.
    if tbl.get("has_label") and "label" not in translations:
        try:
            base_lbl = await row(db, f'SELECT "label" FROM {qual} WHERE id=$1', int(record_id))
            if base_lbl and base_lbl["label"]:
                raw = base_lbl["label"]
                translations["label"] = raw if isinstance(raw, dict) else {"en": str(raw)}
        except Exception:
            pass  # label column may not exist or may not be jsonb

    return {**record, "_translations": translations}


async def create_record(
    db: asyncpg.Connection,
    table_code: str,
    data: dict,
    user: str,
    lang: str | None = None,
) -> dict:
    """Insert a new record.

    is_multilingual fields are written as jsonb dicts directly to their column.
    The separate translations table is no longer used.
    """
    tbl, flds = await load_schema(db, table_code)
    if tbl.get("is_system"):
        raise ValueError(f"'{table_code}' is a system table and is read-only")
    await _set_current_user(db, user)
    await set_request_lang(db, lang)

    tbl_name = safe_name(table_code)
    schema   = tbl.get("schema_name") or DEFAULT_SCHEMA
    qual     = f"{schema}.{tbl_name}"

    for f in flds:
        if f["is_system"] or f["is_multilingual"] or f["field_type"] in FIELD_TYPES_NO_COLUMN:
            continue
        if f["is_required"] and data.get(f["code"]) is None and f.get("default_value") is None:
            raise ValueError(f"'{f['code']}' is required")

    code = (data.get("code") or "").strip()
    if not code:
        raise ValueError("code is required")

    col_names: list[str] = ["code", "is_active", "sort_order"]
    col_vals:  list      = [code, bool(data.get("is_active", True)), int(data.get("sort_order", 0))]

    # has_label: map _label_translations → label column (jsonb)
    if tbl.get("has_label"):
        label_raw = data.get("label") or data.get("_label_translations")
        if label_raw is not None:
            if not isinstance(label_raw, dict):
                label_raw = {"en": str(label_raw)}
            col_names.append("label")
            col_vals.append(_to_jsonb_str(label_raw))

    for f in flds:
        fc = f["code"]
        if fc in _WRITABLE_SKIP or f["is_system"] or fc == "label":
            continue
        if f["field_type"] in FIELD_TYPES_NO_COLUMN:
            continue
        if f["field_type"] == "daterange":
            col_names += [f"{fc}_from", f"{fc}_to"]
            col_vals  += [data.get(f"{fc}_from"), data.get(f"{fc}_to")]
            continue
        if pg_type(f) is None:
            continue

        val_raw = data.get(fc)
        if f["is_multilingual"]:
            if val_raw is None:
                continue
            col_names.append(fc)
            col_vals.append(_to_jsonb_str(val_raw if isinstance(val_raw, dict) else {"en": str(val_raw)}))
        else:
            col_names.append(fc)
            col_vals.append(val_raw)

    placeholders = ", ".join(f"${i+1}" for i in range(len(col_vals)))
    insert_sql   = f'INSERT INTO {qual} ({", ".join(col_names)}) VALUES ({placeholders}) RETURNING id'
    new_id       = await val(db, insert_sql, *col_vals)

    # Multiselect junction tables
    for f in flds:
        if f["field_type"] != "multiselect":
            continue
        values = data.get(f["code"]) or []
        if not values:
            continue
        jt = f"{qual}_{safe_name(f['code'])}_links"
        for idx, target_val in enumerate(values):
            await execute(
                db,
                f"INSERT INTO {jt} (source_id, target_value, sort_order) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                new_id, str(target_val), idx,
            )

    return await get_record(db, table_code, new_id, lang)


async def update_record(
    db: asyncpg.Connection,
    table_code: str,
    record_id: int,
    data: dict,
    user: str,
    lang: str | None = None,
) -> dict:
    """Update an existing record.

    is_multilingual fields are patched as jsonb dicts directly on their column.
    """
    tbl, flds = await load_schema(db, table_code)
    if tbl.get("is_system"):
        raise ValueError(f"'{table_code}' is a system table and is read-only")
    await _set_current_user(db, user)
    await set_request_lang(db, lang)

    tbl_name = safe_name(table_code)
    schema   = tbl.get("schema_name") or DEFAULT_SCHEMA
    qual     = f"{schema}.{tbl_name}"

    set_parts: list[str] = []
    set_vals:  list      = []

    # has_label: map _label_translations → label column (jsonb)
    if tbl.get("has_label"):
        label_raw = data.get("label") or data.get("_label_translations")
        if label_raw is not None:
            if not isinstance(label_raw, dict):
                label_raw = {"en": str(label_raw)}
            set_vals.append(_to_jsonb_str(label_raw))
            set_parts.append(f'"label"=${len(set_vals)}')

    for f in flds:
        fc = f["code"]
        if fc in _WRITABLE_SKIP or f["is_system"] or fc == "label":
            continue
        if f["field_type"] in FIELD_TYPES_NO_COLUMN:
            continue
        if f["field_type"] == "daterange":
            for suffix in ("_from", "_to"):
                key = f"{fc}{suffix}"
                if key in data:
                    set_vals.append(data[key])
                    set_parts.append(f'"{key}"=${len(set_vals)}')
            continue
        if pg_type(f) is None:
            continue
        if fc not in data:
            continue

        val_raw = data[fc]
        if f["is_multilingual"]:
            set_vals.append(_to_jsonb_str(val_raw if isinstance(val_raw, dict) else {"en": str(val_raw)}))
        else:
            set_vals.append(val_raw)
        set_parts.append(f'"{fc}"=${len(set_vals)}')

    for col in ("code", "is_active", "sort_order"):
        if col in data:
            set_vals.append(data[col])
            set_parts.append(f'"{col}"=${len(set_vals)}')

    if set_parts:
        set_vals.append(int(record_id))
        await execute(
            db,
            f"UPDATE {qual} SET {', '.join(set_parts)} WHERE id=${len(set_vals)}",
            *set_vals,
        )

    # Multiselect junction tables
    for f in flds:
        if f["field_type"] != "multiselect" or f["code"] not in data:
            continue
        jt = f"{qual}_{safe_name(f['code'])}_links"
        await execute(db, f"DELETE FROM {jt} WHERE source_id=$1", int(record_id))
        for idx, target_val in enumerate(data[f["code"]] or []):
            await execute(
                db,
                f"INSERT INTO {jt} (source_id, target_value, sort_order) VALUES ($1,$2,$3)",
                int(record_id), str(target_val), idx,
            )

    return await get_record(db, table_code, int(record_id), lang)


async def delete_record(
    db: asyncpg.Connection,
    table_code: str,
    record_id: int,
    soft: bool = True,
    user: str | None = None,
) -> dict:
    tbl, _ = await load_schema(db, table_code)
    if tbl.get("is_system"):
        raise ValueError(f"'{table_code}' is a system table and is read-only")
    tbl_name = safe_name(table_code)
    schema   = tbl.get("schema_name") or DEFAULT_SCHEMA
    qual     = f"{schema}.{tbl_name}"

    if soft:
        await execute(db, f"UPDATE {qual} SET is_active=FALSE WHERE id=$1", int(record_id))
    else:
        await execute(db, f"DELETE FROM {qual} WHERE id=$1", int(record_id))

    return {"deleted": True, "soft": soft, "id": record_id}


async def get_ref_options(
    db: asyncpg.Connection,
    table_code: str,
    search: str | None = None,
    limit: int = 100,
    lang: str | None = None,
) -> list:
    tbl, _ = await load_schema(db, table_code)
    schema  = tbl.get("schema_name") or DEFAULT_SCHEMA
    await set_request_lang(db, lang)
    from app.modules.dbtoolkit.core.constants import SQL_MAX_ROWS
    limit = min(limit, SQL_MAX_ROWS)
    return await rows(
        db,
        f"""SELECT code AS value, label
            FROM {schema}.v_{safe_name(table_code)}
            WHERE is_active=TRUE
              AND ($1::text IS NULL OR code ILIKE '%'||$1||'%' OR label ILIKE '%'||$1||'%')
            ORDER BY sort_order, code LIMIT $2""",
        search, limit,
    )
