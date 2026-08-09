"""
Module: toolkit.services.table_service
Purpose: Business logic for toolkit table management — creation, update,
         deletion, view regeneration, and schema context aggregation.

All database interactions and DDL orchestration live here.
Routers call these functions and wrap results in ok()/err().
"""
import json
import logging
from datetime import datetime, timezone

import asyncpg

from app.core.database import row, rows, execute
from app.modules.dbtoolkit.core.constants import DEFAULT_SCHEMA, SYSTEM_FIELD_CODES
from app.modules.dbtoolkit.core.ddl import (
    build_full_ddl,
    build_create_view,
    build_list_function,
    build_drop_table,
    build_add_column,
    build_alter_column,
    build_drop_column,
    pg_type,
)
from app.modules.dbtoolkit.core.ddl.builder import build_write_functions
from app.modules.dbtoolkit.core.ddl.types import safe_name
from app.modules.dbtoolkit.db import exec_ddl, flush_ddl_log, conn_set_user, actual_cols as get_actual_cols, actual_col_types as get_actual_col_types
from app.modules.api_bridge.engine.registry import registry as _engine_registry
from app.modules.dbtoolkit.services.activity_service import (
    log_activity,
    TABLE_CREATED, TABLE_UPDATED, TABLE_DELETED,
)

logger = logging.getLogger(__name__)


async def _custom_sql(
    db: asyncpg.Connection,
    schema_name: str,
    code: str,
    object_type: str,
) -> str | None:
    """Return user-saved DDL from toolkit_objects, or None if no custom version exists.

    When the user edits a view/function in the DDL Preview tab and clicks Apply,
    sql_service.execute_sql saves the SQL to toolkit_objects. This helper checks
    for that saved version so regenerate/update_table can preserve it instead of
    overwriting with auto-generated SQL.
    """
    return await db.fetchval(
        "SELECT sql FROM toolkit_objects "
        "WHERE schema_name = $1 AND code = $2 AND object_type = $3 AND is_active = TRUE",
        schema_name, code, object_type,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_table_metadata(table: dict, fields: list, options: list) -> dict:
    """Build a clean JSON snapshot of a table definition for AI context.

    Args:
        table: Table dict from toolkit_tables.
        fields: List of field dicts from v_toolkit_fields.
        options: List of option dicts from toolkit_field_options.

    Returns:
        Dict suitable for storing as metadata_json on the table record.
    """
    user_fields = [f for f in fields if not f.get("is_system")]
    opts_by_field: dict[int, list] = {}
    for o in options:
        fid = o.get("field_id")
        if fid:
            opts_by_field.setdefault(fid, []).append(o)

    field_defs = []
    for f in user_fields:
        entry: dict = {
            "code":        f["code"],
            "label":       f.get("label") or f["code"],
            "field_type":  f["field_type"],
            "is_required": bool(f.get("is_required")),
            "is_unique":   bool(f.get("is_unique")),
        }
        if f.get("description"):
            entry["description"] = f["description"]
        if f.get("default_value") is not None:
            entry["default_value"] = f["default_value"]
        cfg = f.get("config") or {}
        if f["field_type"] == "inline_select":
            entry["options"] = [
                {"code": o["code"], "label": o["label"]}
                for o in opts_by_field.get(f.get("id"), [])
                if o.get("code")
            ]
        elif f["field_type"] in ("select", "multiselect") and f.get("ref_table_code"):
            entry["ref_table_code"] = f["ref_table_code"]
            entry["store_field"]    = f.get("store_field") or "code"
            entry["display_field"]  = f.get("display_field") or "label"
        elif f["field_type"] == "computed" and cfg.get("expression"):
            entry["expression"] = cfg["expression"]
        elif f["field_type"] == "toggle":
            if cfg.get("true_label"):  entry["true_label"]  = cfg["true_label"]
            if cfg.get("false_label"): entry["false_label"] = cfg["false_label"]
        field_defs.append(entry)

    return {
        "code":        table["code"],
        "label":       table.get("label") or table["code"],
        "description": table.get("description") or None,
        "schema_name": table.get("schema_name") or DEFAULT_SCHEMA,
        "has_label":   bool(table.get("has_label", True)),
        "fields":      field_defs,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }


async def save_metadata(
    db: asyncpg.Connection,
    table_id: int,
    *,
    _tbl: dict | None = None,
    _flds: list | None = None,
) -> None:
    """Build and persist the metadata JSON snapshot for one table.

    Pass _tbl and _flds when they are already loaded to skip the re-reads.

    Args:
        db: Active database connection.
        table_id: Primary key of the table in toolkit_tables.
        _tbl: Pre-loaded table dict (optional — avoids a SELECT).
        _flds: Pre-loaded fields list (optional — avoids a SELECT).
    """
    tbl = _tbl or await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        return
    flds = _flds if _flds is not None else await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order, code",
        table_id,
    )
    opts = await rows(
        db,
        """SELECT fo.* FROM toolkit_field_options fo
           JOIN toolkit_fields f ON f.id=fo.field_id
           WHERE f.table_id=$1 ORDER BY fo.field_id, fo.sort_order""",
        table_id,
    )
    metadata = _build_table_metadata(dict(tbl), [dict(f) for f in flds], [dict(o) for o in opts])
    await execute(
        db,
        "UPDATE toolkit_tables SET metadata_json=$1 WHERE id=$2",
        json.dumps(metadata), table_id,
    )


async def load_table_with_fields(
    db: asyncpg.Connection,
    table_id: int,
) -> tuple[dict, list]:
    """Fetch a table and its fields from the catalog.

    Args:
        db: Active database connection.
        table_id: Primary key in toolkit_tables.

    Returns:
        Tuple of (table_dict, [field_dicts]).

    Raises:
        ValueError: If the table does not exist.
    """
    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        raise ValueError("Table not found")
    flds = await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order, code",
        table_id,
    )
    return tbl, flds


async def save_table_translations(
    db: asyncpg.Connection,
    table_id: int,
    fields: list,
    user: str,
) -> dict:
    """Save ONLY label/description/toggle i18n translations for a table's fields.

    Does NOT touch the physical schema — no DDL, no column adds/drops, no field
    deletions.  Safe to call from the Translation tab without any schema changes.
    Only updates toolkit_fields.config (label_i18n, description_i18n, and toggle
    true_label_i18n / false_label_i18n) for each field identified by code.
    """
    for f in fields:
        code = (f.get("code") or "").strip().lower()
        if not code:
            continue
        fld = await row(
            db,
            "SELECT id, field_type, config FROM toolkit_fields WHERE table_id=$1 AND code=$2",
            table_id, code,
        )
        if not fld:
            continue

        cfg = dict(fld["config"] or {})
        changed = False

        # Field label translations (top-level key in the payload)
        i18n = f.get("label_i18n")
        if i18n is not None:
            cfg["label_i18n"] = {k: v for k, v in (i18n or {}).items() if v}
            changed = True

        # Field description translations
        d18n = f.get("description_i18n")
        if d18n is not None:
            cfg["description_i18n"] = {k: v for k, v in (d18n or {}).items() if v}
            changed = True

        # Toggle value translations (carried inside config key in the payload)
        if fld["field_type"] == "toggle":
            payload_cfg = f.get("config") or {}
            if "true_label_i18n" in payload_cfg:
                cfg["true_label_i18n"] = {k: v for k, v in (payload_cfg["true_label_i18n"] or {}).items() if v}
                changed = True
            if "false_label_i18n" in payload_cfg:
                cfg["false_label_i18n"] = {k: v for k, v in (payload_cfg["false_label_i18n"] or {}).items() if v}
                changed = True

        if changed:
            await execute(
                db,
                "UPDATE toolkit_fields SET config=$1, modified_at=now() WHERE id=$2",
                json.dumps(cfg), fld["id"],
            )

        # inline_select option label translations
        if fld["field_type"] == "inline_select":
            for opt_payload in (f.get("options") or []):
                opt_id_raw = opt_payload.get("id")
                opt_i18n   = opt_payload.get("label_i18n")
                if not opt_id_raw or not opt_i18n:
                    continue
                try:
                    opt_id = int(opt_id_raw)
                except (TypeError, ValueError):
                    continue
                clean = {k: v for k, v in opt_i18n.items() if v}
                if clean:
                    await execute(
                        db,
                        "UPDATE toolkit_field_options SET label_i18n=$1 WHERE id=$2",
                        json.dumps(clean), opt_id,
                    )

    tbl, flds = await load_table_with_fields(db, table_id)
    opts = await rows(
        db,
        """SELECT fo.* FROM toolkit_field_options fo
           JOIN toolkit_fields f ON f.id=fo.field_id
           WHERE f.table_id=$1 ORDER BY fo.field_id, fo.sort_order""",
        table_id,
    )
    return {**dict(tbl), "fields": [dict(f) for f in flds], "options": [dict(o) for o in opts]}


async def fetch_ref_fields(
    db: asyncpg.Connection,
    fields: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Fetch field definitions and schema info for all referenced toolkit tables.

    For every select/multiselect field that references another toolkit table,
    fetch that table's fields so build_create_view can determine whether the
    display field is multilingual, and fetch the schema_name so the view JOIN
    can use a fully-qualified table reference when the table is not in public.

    Only tables that physically exist in the database are included — avoids
    view creation failures when a referenced table is catalogued but not yet
    created.

    Args:
        db: Active database connection.
        fields: List of field dicts to inspect for ref_table_code.

    Returns:
        Tuple of:
          - fields_map: { ref_table_code: [field_dicts] }
          - schema_map: { ref_table_code: schema_name }
    """
    ref_codes = {
        f["ref_table_code"]
        for f in fields
        if f.get("ref_table_code") and not f.get("is_system")
    }
    if not ref_codes:
        return {}, {}

    # Batch query 1: catalog metadata for all referenced tables at once
    ref_tbl_rows = await rows(
        db,
        "SELECT id, code, schema_name FROM toolkit_tables WHERE code = ANY($1) AND is_active=true",
        list(ref_codes),
    )
    if not ref_tbl_rows:
        return {}, {}

    ref_by_code = {r["code"]: r for r in ref_tbl_rows}

    # Batch query 2: physical existence check for all ref tables at once
    # Build pairs (schema, table_name) and check with a single pg_catalog scan
    schema_by_code = {
        code: (tbl["schema_name"] or DEFAULT_SCHEMA)
        for code, tbl in ref_by_code.items()
    }
    schema_table_pairs = [(schema_by_code[c], c) for c in ref_by_code]
    existing_rows = await rows(
        db,
        """SELECT n.nspname AS schema_name, c.relname AS table_name
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'r'
             AND (n.nspname, c.relname) IN (SELECT s, t FROM unnest($1::text[], $2::text[]) AS u(s, t))""",
        [p[0] for p in schema_table_pairs],
        [p[1] for p in schema_table_pairs],
    )
    existing_codes = {r["table_name"] for r in existing_rows}

    # Batch query 3: fields for all physically-existing ref tables at once
    valid_ids = [ref_by_code[c]["id"] for c in existing_codes if c in ref_by_code]
    if not valid_ids:
        return {}, {}

    all_ref_flds = await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id = ANY($1) ORDER BY sort_order, code",
        valid_ids,
    )
    id_to_code = {ref_by_code[c]["id"]: c for c in existing_codes if c in ref_by_code}

    fields_map: dict[str, list[dict]] = {}
    schema_map: dict[str, str] = {}
    for fld in all_ref_flds:
        tbl_id = fld["table_id"]
        if tbl_id in id_to_code:
            code = id_to_code[tbl_id]
            fields_map.setdefault(code, []).append(dict(fld))
            schema_map[code] = schema_by_code[code]

    return fields_map, schema_map


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

async def get_schema_context(
    db: asyncpg.Connection,
    codes: list[str] | None = None,
) -> dict:
    """Return full schema context for AI — tables and objects.

    Reads toolkit_tables.metadata_json and toolkit_objects.metadata_json.
    Filtered by optional codes list (table codes only).

    Args:
        db: Active database connection.
        codes: Optional list of table codes to restrict tables section.

    Returns:
        Dict with keys 'tables', 'objects', and 'count'.
    """
    if codes:
        tbl_rows = await rows(
            db,
            "SELECT code, metadata_json FROM toolkit_tables "
            "WHERE is_active=true AND code=ANY($1::text[]) ORDER BY code",
            codes,
        )
    else:
        tbl_rows = await rows(
            db,
            "SELECT code, metadata_json FROM toolkit_tables "
            "WHERE is_active=true AND metadata_json IS NOT NULL ORDER BY code",
        )

    obj_rows = await rows(
        db,
        "SELECT code, object_type, metadata_json FROM toolkit_objects "
        "WHERE is_active=true AND metadata_json IS NOT NULL ORDER BY object_type, code",
    )

    tables = []
    for r in tbl_rows:
        meta = r["metadata_json"]
        if meta:
            entry = meta if isinstance(meta, dict) else json.loads(meta)
            entry["_kind"] = "table"
            tables.append(entry)

    objects = []
    for r in obj_rows:
        meta = r["metadata_json"]
        if meta:
            entry = meta if isinstance(meta, dict) else json.loads(meta)
            entry["_kind"] = r["object_type"]
            entry.setdefault("code", r["code"])
            objects.append(entry)

    return {"tables": tables, "objects": objects, "count": len(tables) + len(objects)}


async def list_tables(db: asyncpg.Connection, is_active: bool | None = None) -> list:
    """Return all toolkit tables with their field counts.

    Args:
        db: Active database connection.
        is_active: Optional filter; None returns all.

    Returns:
        List of table dicts augmented with 'field_count'.
    """
    return await rows(
        db,
        """SELECT t.*,
               (SELECT COUNT(*)::int FROM toolkit.toolkit_fields f WHERE f.table_id = t.id) AS field_count
           FROM toolkit.v_toolkit_tables t
           WHERE ($1::bool IS NULL OR t.is_active = $1)
             AND t.schema_name != 'admin'""",
        is_active,
    )


async def get_table(
    db: asyncpg.Connection,
    id: int | None = None,
    code: str | None = None,
) -> dict:
    """Fetch a single table with its fields and options.

    Args:
        db: Active database connection.
        id: Table primary key (preferred).
        code: Table code (fallback).

    Returns:
        Table dict with 'fields' and 'options' lists.

    Raises:
        ValueError: If neither id nor code is given, or the table is not found.
    """
    if id is not None:
        tbl = await row(db, "SELECT * FROM toolkit.v_toolkit_tables WHERE id=$1 AND schema_name != 'admin'", id)
    elif code is not None:
        tbl = await row(db, "SELECT * FROM toolkit.v_toolkit_tables WHERE code=$1 AND schema_name != 'admin'", code)
    else:
        raise ValueError("id or code required")
    if not tbl:
        raise ValueError("Table not found")

    flds = await rows(
        db,
        "SELECT * FROM toolkit.v_toolkit_fields WHERE table_id=$1 ORDER BY sort_order",
        tbl["id"],
    )
    opts = await rows(
        db,
        """SELECT fo.* FROM toolkit_field_options fo
           JOIN toolkit_fields f ON f.id=fo.field_id
           WHERE f.table_id=$1 ORDER BY fo.field_id, fo.sort_order""",
        tbl["id"],
    )
    return {**tbl, "fields": flds, "options": opts}


async def create_table(
    db: asyncpg.Connection,
    table: dict,
    fields: list,
    options: list,
    user: str,
) -> dict:
    """Create a new toolkit-managed table and execute DDL.

    Inserts the catalog record, creates all fields, runs CREATE TABLE +
    VIEW + FUNCTION DDL, and saves the metadata snapshot.

    Args:
        db: Active database connection.
        table: Dict with at minimum 'code'; optional schema/label/description etc.
        fields: List of field dicts from the request.
        options: List of option dicts (currently unused at create time).
        user: Email of the acting admin, used for audit logging.

    Returns:
        Complete table dict with 'fields' list.

    Raises:
        ValueError: If table code is missing or already exists.
    """
    code        = (table.get("code") or "").strip().lower()
    label       = (table.get("label") or code).strip()
    schema_name = (table.get("schema_name") or DEFAULT_SCHEMA).strip()

    if not code:
        raise ValueError("code is required")
    if schema_name == "admin":
        raise ValueError("Tables cannot be created in the 'admin' schema via the toolkit API")

    # Validation 1 — no duplicate field codes in the incoming request
    _seen: set[str] = set()
    for f in fields:
        fc = (f.get("code") or "").strip().lower()
        if fc and fc in _seen:
            raise ValueError(f"Duplicate field code '{fc}' in request")
        if fc:
            _seen.add(fc)

    if await row(db, "SELECT id FROM toolkit_tables WHERE code=$1", code):
        raise ValueError(f"Table '{code}' already exists in catalog")

    # Check whether the physical table already exists in the database schema.
    # CREATE TABLE IF NOT EXISTS would silently skip if it does, then subsequent
    # DDL (COMMENT ON COLUMN, trigger) would fail on columns that were never added.
    # Give a clear error here instead of a confusing mid-DDL failure.
    if await row(
        db,
        """SELECT 1 FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'r' AND n.nspname = $1 AND c.relname = $2""",
        schema_name, code,
    ):
        raise ValueError(
            f"A physical table '{schema_name}.{code}' already exists in the database "
            f"but is not registered in the toolkit. Choose a different table code, "
            f"or drop the existing table first if it is no longer needed."
        )

    # All writes — catalog inserts, DDL, metadata — run inside a single transaction.
    # If anything fails (e.g. CREATE TABLE DDL error), the transaction rolls back and
    # no orphaned rows are left in toolkit_tables or toolkit_fields.
    async with db.transaction():
        tbl = await row(
            db,
            """INSERT INTO toolkit_tables
                   (code, schema_name, label, description, icon, is_system, has_label, is_active, sort_order)
               VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,$8) RETURNING *""",
            code,
            schema_name,
            label,
            table.get("description") or None,
            table.get("icon") or None,
            bool(table.get("is_system", False)),
            bool(table.get("has_label", True)),
            int(table.get("sort_order", 0)),
        )

        # ── Auto-inject common fields as system fields ────────────────────────
        # Fetch all active common fields with ALL their translations so that
        # label_i18n / description_i18n are fully seeded into toolkit_fields.config.
        common_translation_rows = await rows(
            db,
            """SELECT cf.id, cf.code, cf.sort_order, cf.field_name,
                      cf.field_type, cf.default_value, cf.show_translation,
                      cf.true_label AS base_true_label, cf.false_label AS base_false_label,
                      cft.lang, cft.label AS tr_label, cft.description AS tr_desc,
                      cft.true_label AS tr_true_label, cft.false_label AS tr_false_label
               FROM toolkit.common_fields cf
               LEFT JOIN toolkit.common_fields_translation cft
                      ON cft.common_field_id = cf.id
               WHERE cf.is_active = TRUE
               ORDER BY cf.sort_order, cf.code, cft.lang""",
        )

        # Group translations by code.
        _cf_meta: dict        = {}   # code → first DB row (carries field meta)
        _cf_labels: dict      = {}   # code → {lang: label}
        _cf_descs: dict       = {}   # code → {lang: description}
        _cf_true_lbls: dict   = {}   # code → {lang: true_label}
        _cf_false_lbls: dict  = {}   # code → {lang: false_label}
        for r in common_translation_rows:
            cf_code = r["code"]
            if cf_code not in _cf_meta:
                _cf_meta[cf_code] = r
            if r.get("lang"):
                if r.get("tr_label"):
                    _cf_labels.setdefault(cf_code, {})[r["lang"]] = r["tr_label"]
                if r.get("tr_desc"):
                    _cf_descs.setdefault(cf_code, {})[r["lang"]] = r["tr_desc"]
                if r.get("tr_true_label"):
                    _cf_true_lbls.setdefault(cf_code, {})[r["lang"]] = r["tr_true_label"]
                if r.get("tr_false_label"):
                    _cf_false_lbls.setdefault(cf_code, {})[r["lang"]] = r["tr_false_label"]

        # System-code fields (id, code, sort_order, …) MUST always come from
        # common_fields with their correct field_type (e.g. "id" → bigserial).
        caller_codes = {
            (f.get("code") or "").strip().lower() for f in fields
            if (f.get("code") or "").strip().lower() not in SYSTEM_FIELD_CODES
        }
        common_field_params = []
        for cf_code, cf in _cf_meta.items():
            if cf_code in caller_codes:
                continue
            label_i18n = _cf_labels.get(cf_code, {})
            desc_i18n  = _cf_descs.get(cf_code, {})
            lbl   = label_i18n.get("en") or cf["field_name"]
            desc  = desc_i18n.get("en")  or lbl
            ftype = cf.get("field_type") or "text"
            if cf_code == "id":
                ftype = "id"
            elif cf_code == "sort_order":
                ftype = "integer"
            default_val = cf.get("default_value")
            if cf_code == "sort_order" and default_val is None:
                default_val = "0"
            config: dict = {}
            if label_i18n:
                config["label_i18n"] = label_i18n
            if desc_i18n:
                config["description_i18n"] = desc_i18n
            # For toggle fields: seed true/false labels and their translations.
            base_true  = cf.get("base_true_label")
            base_false = cf.get("base_false_label")
            if base_true or base_false:
                true_i18n  = dict(_cf_true_lbls.get(cf_code, {}))
                false_i18n = dict(_cf_false_lbls.get(cf_code, {}))
                if base_true:
                    config["true_label"] = base_true
                    true_i18n.setdefault("en", base_true)
                if base_false:
                    config["false_label"] = base_false
                    false_i18n.setdefault("en", base_false)
                if true_i18n:
                    config["true_label_i18n"]  = true_i18n
                if false_i18n:
                    config["false_label_i18n"] = false_i18n
            is_multilingual = bool(cf.get("show_translation", False))
            common_field_params.append((
                tbl["id"],
                cf_code,
                lbl,
                desc,
                ftype,
                cf_code in ("id", "code", "sort_order"),   # is_required
                cf_code == "code",                          # is_unique
                is_multilingual,
                False,
                config,
                default_val,
                cf["sort_order"],
            ))
        if common_field_params:
            await db.executemany(
                """INSERT INTO toolkit_fields
                       (table_id, code, label, description, field_type, is_required,
                        is_unique, is_multilingual, is_system, config, default_value, sort_order)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (table_id, code) DO NOTHING""",
                common_field_params,
            )

        # ── User-supplied fields ──────────────────────────────────────────────
        from app.modules.dbtoolkit.services.reference_service import upsert_reference

        for idx, f in enumerate(fields):
            if not f.get("code") or not f.get("label"):
                continue
            # System fields are always provided by common_fields above; skip duplicates
            if (f.get("code") or "").strip().lower() in SYSTEM_FIELD_CODES:
                continue
            config = dict(f.get("config") or {})
            if f.get("label_i18n"):       config["label_i18n"]       = f["label_i18n"]
            if f.get("description_i18n"): config["description_i18n"] = f["description_i18n"]
            fld = await row(
                db,
                """INSERT INTO toolkit_fields
                       (table_id, code, label, description, field_type, is_required,
                        is_unique, is_multilingual, is_system, config, default_value, sort_order)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (table_id, code) DO UPDATE
                   SET label=$3, description=$4, field_type=$5, is_required=$6,
                       is_unique=$7, is_multilingual=$8, is_system=$9,
                       config=$10, default_value=$11, sort_order=$12
                   RETURNING id""",
                tbl["id"], f["code"], f["label"],
                f.get("description", ""),
                f.get("field_type", "text"),
                bool(f.get("is_required", False)),
                bool(f.get("is_unique", False)),
                bool(f.get("is_multilingual", False)),
                bool(f.get("is_system", False)),
                config,
                f.get("default_value"),
                f.get("sort_order", idx),
            )

            # Save reference config for select/multiselect fields
            if fld and f.get("field_type") in ("select", "multiselect"):
                ref_table_id   = f.get("ref_table_id")
                ref_table_code = f.get("ref_table_code")
                if not ref_table_id and ref_table_code:
                    ref_tbl_row = await row(db, "SELECT id FROM toolkit_tables WHERE code=$1", ref_table_code)
                    if ref_tbl_row:
                        ref_table_id = ref_tbl_row["id"]
                if ref_table_id:
                    try:
                        await upsert_reference(db, {
                            "field_id":          fld["id"],
                            "ref_table_id":      ref_table_id,
                            "store_field":       f.get("store_field") or "code",
                            "display_field":     f.get("display_field") or "label",
                            "display_template":  f.get("display_template"),
                            "search_fields":     f.get("search_fields") or ["code", "label"],
                            "filter_conditions": f.get("filter_conditions"),
                            "order_by":          f.get("order_by") or "sort_order, code",
                            "ref_type":          f.get("ref_type", "single"),
                            "cascade_on_delete": f.get("cascade_on_delete", "restrict"),
                            "performed_by":      user,
                        })
                    except Exception as exc:
                        logger.warning(
                            "ref config save failed for %s.%s: %s", code, f["code"], exc
                        )

        tbl_row, flds = await load_table_with_fields(db, tbl["id"])
        await conn_set_user(db, user)

        ref_map, schema_map = await fetch_ref_fields(db, flds)
        ddl_steps = build_full_ddl(tbl_row, flds, ref_fields_map=ref_map, ref_schema_map=schema_map)
        _ddl_log: list = []
        # Create the target schema if it doesn't exist yet — schemas are never
        # auto-created on startup so this is the first opportunity to do it.
        await exec_ddl(db, f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";', code, "create_schema", user, _log_batch=_ddl_log)
        for op, sql in ddl_steps:
            if op in ("create_view", "create_function"):
                continue  # views and functions are managed manually via the DDL tab
            logger.debug("create_table DDL [%s/%s]: %.400s", code, op, sql)
            await exec_ddl(db, sql, code, op, user, _log_batch=_ddl_log)
        await flush_ddl_log(db, _ddl_log)

        await save_metadata(db, tbl["id"], _tbl=dict(tbl_row), _flds=list(flds))
        await db.fetchval("SELECT toolkit.fn_register_table($1, $2)", schema_name, code)
        await log_activity(
            db, TABLE_CREATED, "table", user,
            entity_id=tbl["id"], entity_code=code, schema_name=schema_name,
            detail={"field_count": len(flds)},
        )

    # Invalidate the in-memory API registry cache after the transaction commits.
    await _engine_registry.invalidate(f"{schema_name}.{code}")
    from app.db.schema_export import schedule_schema_export
    schedule_schema_export()
    return {**tbl_row, "fields": flds}


async def update_table(
    db: asyncpg.Connection,
    table_id: int,
    table: dict,
    fields: list,
    options: list,
    user: str,
) -> dict:
    """Update an existing toolkit table's metadata and fields.

    Handles field upserts, column adds/alters, view/function regeneration,
    and metadata snapshot update.

    Args:
        db: Active database connection.
        table_id: Primary key of the table to update.
        table: Dict of table-level updates (label, description, icon, etc.).
        fields: List of updated field dicts.
        options: List of option dicts (currently passed through).
        user: Email of the acting admin.

    Returns:
        Complete updated table dict with 'fields' and 'options'.

    Raises:
        ValueError: If the table is not found.
    """
    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        raise ValueError("Table not found")
    if tbl["is_system"]:
        raise ValueError("System tables cannot be modified")
    if tbl["schema_name"] == "admin":
        raise ValueError("Admin tables cannot be modified via the toolkit API")

    updated = await row(
        db,
        """UPDATE toolkit_tables
           SET label=$1, description=$2, icon=$3, is_active=TRUE, sort_order=$4, modified_at=now()
           WHERE id=$5 RETURNING *""",
        table.get("label", tbl["label"]),
        table.get("description", tbl["description"]),
        table.get("icon", tbl["icon"]),
        int(table.get("sort_order", tbl["sort_order"])),
        table_id,
    )

    if fields:
        await conn_set_user(db, user)

        # Duplicate field code check
        _seen: set[str] = set()
        for f in fields:
            fc = (f.get("code") or "").strip().lower()
            if fc and fc in _seen:
                raise ValueError(f"Duplicate field code '{fc}' in request")
            if fc:
                _seen.add(fc)

        existing_fields_map = {
            r["code"]: dict(r) for r in await rows(
                db,
                "SELECT code, field_type, is_multilingual, is_system, config FROM toolkit_fields WHERE table_id=$1",
                table_id,
            )
        }
        existing_codes = set(existing_fields_map.keys())

        # Protected codes — three sources so scaffold/common fields are never deleted:
        #  1. SYSTEM_FIELD_CODES: hardcoded constant (includes is_active)
        #  2. DB rows with is_system=TRUE: self-healed on each successful save
        #  3. Payload fields with is_system=true: frontend marks all scaffold fields
        protected_codes = (
            SYSTEM_FIELD_CODES
            | {code for code, fld in existing_fields_map.items() if fld.get("is_system")}
            | {(f.get("code") or "").strip().lower() for f in fields if f.get("is_system")}
        )

        # Fetch actual DB column types once so we detect drift between stored
        # metadata and the real schema (e.g. a field that changed pg_type()
        # output after a code update but was never re-migrated).
        actual_db_col_types = await get_actual_col_types(db, dict(updated))

        from app.modules.dbtoolkit.services.reference_service import upsert_reference

        for idx, f in enumerate(fields):
            if not f.get("code") or not f.get("label"):
                continue
            # Protected fields (system + scaffold/common) — never touch DDL or
            # field_type/constraints.  Only update description and label_i18n.
            if (f.get("code") or "").strip().lower() in protected_codes:
                code_lc = (f.get("code") or "").strip().lower()
                prot_fld = await row(
                    db,
                    "SELECT id, config FROM toolkit_fields WHERE table_id=$1 AND code=$2",
                    updated["id"], code_lc,
                )
                if prot_fld:
                    cfg = dict(prot_fld["config"] or {})
                    if f.get("label_i18n"):
                        cfg["label_i18n"] = f["label_i18n"]
                    await execute(
                        db,
                        "UPDATE toolkit_fields SET description=$1, config=$2, is_system=TRUE, modified_at=now() WHERE id=$3",
                        f.get("description", ""), json.dumps(cfg), prot_fld["id"],
                    )
                continue

            code     = f["code"]
            is_new   = code not in existing_codes
            old_f    = existing_fields_map.get(code)
            new_type = f.get("field_type", "text")

            config = dict(f.get("config") or {})
            if f.get("label_i18n"):       config["label_i18n"]       = f["label_i18n"]
            if f.get("description_i18n"): config["description_i18n"] = f["description_i18n"]
            await execute(
                db,
                """INSERT INTO toolkit_fields
                       (table_id, code, label, description, field_type, is_required,
                        is_unique, is_multilingual, is_system, config, default_value, sort_order)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (table_id, code) DO UPDATE
                   SET label=$3, description=$4, field_type=$5, is_required=$6,
                       is_unique=$7, is_multilingual=$8, is_system=$9,
                       config=$10, default_value=$11, sort_order=$12""",
                updated["id"], code, f["label"],
                f.get("description", ""),
                new_type,
                bool(f.get("is_required", False)),
                bool(f.get("is_unique", False)),
                bool(f.get("is_multilingual", False)),
                bool(f.get("is_system", False)),
                config,
                f.get("default_value"),
                f.get("sort_order", idx),
            )

            fld_row = await row(
                db, "SELECT * FROM toolkit_fields WHERE table_id=$1 AND code=$2",
                updated["id"], code,
            )
            if not fld_row:
                continue
            fld_dict = dict(fld_row)
            new_pg   = pg_type(fld_dict)
            # Use the actual DB column type (not the metadata-derived type) so
            # we detect drift when pg_type() output changed after a code update.
            old_pg   = actual_db_col_types.get(safe_name(code)) if not is_new else None

            # Save reference config for select/multiselect fields when ref data is present
            if new_type in ("select", "multiselect"):
                ref_table_id   = f.get("ref_table_id")
                ref_table_code = f.get("ref_table_code")
                if not ref_table_id and ref_table_code:
                    ref_tbl_row = await row(
                        db, "SELECT id FROM toolkit_tables WHERE code=$1", ref_table_code
                    )
                    if ref_tbl_row:
                        ref_table_id = ref_tbl_row["id"]
                if ref_table_id:
                    try:
                        await upsert_reference(db, {
                            "field_id":          fld_row["id"],
                            "ref_table_id":      ref_table_id,
                            "store_field":       f.get("store_field") or "code",
                            "display_field":     f.get("display_field") or "label",
                            "display_template":  f.get("display_template"),
                            "search_fields":     f.get("search_fields") or ["code", "label"],
                            "filter_conditions": f.get("filter_conditions"),
                            "order_by":          f.get("order_by") or "sort_order, code",
                            "ref_type":          f.get("ref_type", "single"),
                            "cascade_on_delete": f.get("cascade_on_delete", "restrict"),
                            "performed_by":      user,
                        })
                    except Exception as exc:
                        logger.warning(
                            "ref config save failed for %s.%s: %s",
                            updated["code"], code, exc,
                        )

            is_pk = (new_type == "id" and code == "id")
            if new_pg is not None and not is_pk:
                try:
                    await exec_ddl(
                        db, build_add_column(dict(updated), fld_dict),
                        updated["code"], "add_column", user,
                    )
                except Exception as exc:
                    logger.error("add_column failed for %s.%s: %s", updated["code"], code, exc)
                    raise ValueError(f"Could not add column '{code}' to '{updated['code']}': {exc}") from exc

                if not is_new and old_pg is not None and old_pg != new_pg:
                    try:
                        await exec_ddl(
                            db, build_alter_column(dict(updated), fld_dict),
                            updated["code"], "alter_column", user,
                        )
                    except Exception as exc:
                        logger.error("alter_column failed for %s.%s: %s", updated["code"], code, exc)
                        raise ValueError(f"Could not change type of column '{code}' in '{updated['code']}': {exc}") from exc

        # ── Remove user fields that are no longer in the incoming payload ─────
        # The frontend sends the complete surviving field list on every save.
        # Any field that was in the catalog but is absent from the payload was
        # deleted by the user and must be removed from both toolkit_fields and
        # the physical schema table.
        incoming_user_codes = {
            (f.get("code") or "").strip().lower()
            for f in fields
            if (f.get("code") or "").strip().lower() not in protected_codes
        }
        existing_user_codes = existing_codes - protected_codes
        for del_code in sorted(existing_user_codes - incoming_user_codes):
            del_fld = await row(
                db,
                "SELECT id, field_type, is_multilingual, config FROM toolkit_fields WHERE table_id=$1 AND code=$2",
                table_id, del_code,
            )
            if not del_fld:
                continue
            del_fld = dict(del_fld)
            # Remove dependent catalog rows before the field row (FK order)
            await execute(db, "DELETE FROM toolkit_field_options WHERE field_id=$1", del_fld["id"])
            # Drop the physical column(s); CASCADE removes any views that reference the column
            ftype = del_fld["field_type"]
            if ftype == "daterange":
                # Daterange creates two physical columns: <code>_from and <code>_to
                for suffix in ("_from", "_to"):
                    try:
                        await exec_ddl(
                            db,
                            build_drop_column(dict(updated), del_code + suffix),
                            updated["code"], "drop_column", user,
                        )
                    except Exception as exc:
                        logger.warning(
                            "drop_column failed for %s.%s%s: %s",
                            updated["code"], del_code, suffix, exc,
                        )
            elif ftype == "multiselect":
                # Multiselect stores values in a junction table, not a column
                jt = f"{safe_name(updated['code'])}_{safe_name(del_code)}_links"
                try:
                    await exec_ddl(
                        db, f"DROP TABLE IF EXISTS {jt} CASCADE;",
                        updated["code"], "drop_column", user,
                    )
                except Exception as exc:
                    logger.warning("drop junction table %s failed: %s", jt, exc)
            elif pg_type(del_fld) is not None:
                # All other types with a physical column (text, integer, jsonb, etc.)
                try:
                    await exec_ddl(
                        db,
                        build_drop_column(dict(updated), del_code),
                        updated["code"], "drop_column", user,
                    )
                except Exception as exc:
                    logger.warning(
                        "drop_column failed for %s.%s: %s", updated["code"], del_code, exc
                    )
            # Remove the catalog row last (after DDL so metadata is still accessible)
            await execute(
                db, "DELETE FROM toolkit_fields WHERE table_id=$1 AND code=$2",
                table_id, del_code,
            )

    # Rebuild the view and list/get functions after all field DDL.
    # This is required because build_alter_column drops the view via CASCADE,
    # and new/removed fields change the view's column list.
    from app.modules.dbtoolkit.services.field_service import reload_table_views
    try:
        await reload_table_views(db, table_id, user)
    except Exception as exc:
        logger.error("reload_table_views failed in update_table for %s: %s", updated["code"], exc)
        raise ValueError(f"View rebuild failed for '{updated['code']}': {exc}") from exc

    await save_metadata(db, updated["id"])
    await db.fetchval(
        "SELECT toolkit.fn_register_table($1, $2)",
        updated["schema_name"], updated["code"],
    )
    await _engine_registry.invalidate(f"{updated['schema_name']}.{updated['code']}")
    tbl_full, flds = await load_table_with_fields(db, updated["id"])
    opts = await rows(
        db,
        """SELECT fo.* FROM toolkit_field_options fo
           JOIN toolkit_fields f ON f.id=fo.field_id
           WHERE f.table_id=$1 ORDER BY fo.field_id, fo.sort_order""",
        updated["id"],
    )
    await log_activity(
        db, TABLE_UPDATED, "table", user,
        entity_id=updated["id"], entity_code=updated["code"],
        schema_name=updated.get("schema_name"),
        detail={"field_count": len(flds)},
    )
    from app.db.schema_export import schedule_schema_export
    schedule_schema_export()
    return {**tbl_full, "fields": flds, "options": list(opts)}


def _related_object_codes(table_code: str) -> list[str]:
    """Return the toolkit_objects code names that are conventionally generated for a table."""
    return [
        f"v_{table_code}",
        f"fn_list_{table_code}",
        f"fn_get_{table_code}",
        f"upsert_{table_code}",
        f"sync_{table_code}",
        f"delete_{table_code}",
        f"insert_{table_code}",
    ]


async def get_associated_objects(
    db: asyncpg.Connection,
    table_id: int,
) -> dict:
    """Return views, functions, API gateway endpoints, and page definitions linked to a table.

    Used before deletion to warn the user about dependent objects.
    """
    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        raise ValueError("Table not found")

    schema = tbl["schema_name"]
    code   = tbl["code"]

    related = _related_object_codes(code)

    obj_rows = await rows(
        db,
        """SELECT code, object_type, schema_name
           FROM toolkit_objects
           WHERE schema_name = $1 AND code = ANY($2::text[]) AND is_active = TRUE""",
        schema, related,
    )

    # Endpoints may point at the table itself, its view, or any generated function.
    api_rows = await rows(
        db,
        """SELECT id, name, url_path, method, status
           FROM api_gateway.endpoints
           WHERE db_schema = $1 AND db_object = ANY($2::text[])""",
        schema, [code] + related,
    )

    page_def_rows = await rows(
        db,
        "SELECT id, code, title FROM toolkit.page_definitions WHERE table_code = $1",
        code,
    )

    return {
        "views":     [dict(r) for r in obj_rows if r["object_type"] == "view"],
        "functions": [dict(r) for r in obj_rows if r["object_type"] == "function"],
        "apis":      [dict(r) for r in api_rows],
        "page_defs": [dict(r) for r in page_def_rows],
    }


async def delete_table(
    db: asyncpg.Connection,
    table_id: int,
    user: str,
) -> dict:
    """Delete a toolkit table and all its catalog and DB objects.

    Args:
        db: Active database connection.
        table_id: Primary key of the table to delete.
        user: Email of the acting admin.

    Returns:
        Dict with 'deleted' and 'code' keys.

    Raises:
        ValueError: If the table is not found or is a system table.
    """
    tbl = await row(db, "SELECT * FROM toolkit_tables WHERE id=$1", table_id)
    if not tbl:
        raise ValueError("Table not found")
    if tbl["is_system"]:
        raise ValueError("System tables cannot be deleted")
    if tbl["schema_name"] == "admin":
        raise ValueError("Admin tables cannot be deleted via the toolkit API")

    schema   = tbl["schema_name"]
    api_code = f"{schema}.{tbl['code']}"

    # 1. Drop the physical table (CASCADE removes views / SQL functions that depend on it).
    await exec_ddl(db, build_drop_table(tbl), tbl["code"], "drop_table", user)

    # 1b. Remove toolkit_objects catalog rows for this table's generated objects.
    await execute(
        db,
        "DELETE FROM toolkit_objects WHERE schema_name=$1 AND code=ANY($2::text[])",
        schema, _related_object_codes(tbl["code"]),
    )

    # 1c. Remove api_gateway.endpoints that point at this table, its view, or its functions.
    await execute(
        db,
        "DELETE FROM api_gateway.endpoints WHERE db_schema=$1 AND db_object=ANY($2::text[])",
        schema, [tbl["code"]] + _related_object_codes(tbl["code"]),
    )

    # 1d. Remove page definitions that reference this table.
    await execute(
        db,
        "DELETE FROM toolkit.page_definitions WHERE table_code = $1",
        tbl["code"],
    )

    # 2. Delete inline options for all fields of this table.
    await execute(
        db,
        """DELETE FROM toolkit_field_options
           WHERE field_id IN (SELECT id FROM toolkit_fields WHERE table_id=$1)""",
        table_id,
    )

    # 3. Delete fields (ref config lives in toolkit_fields.config — removed automatically).
    await execute(db, "DELETE FROM toolkit_fields WHERE table_id=$1", table_id)

    # 4. Delete the catalog row — safe now that all FK references are gone.
    await execute(db, "DELETE FROM toolkit_tables WHERE id=$1", table_id)

    await execute(db, "DELETE FROM toolkit.api_objects WHERE code=$1", api_code)
    await _engine_registry.invalidate(api_code)
    await log_activity(
        db, TABLE_DELETED, "table", user,
        entity_code=tbl["code"], schema_name=schema,
    )
    from app.db.schema_export import schedule_schema_export
    schedule_schema_export()
    return {"deleted": True, "code": tbl["code"]}


async def regenerate_view(
    db: asyncpg.Connection,
    table_id: int,
    user: str,
    object_type: str | None = None,
) -> dict:
    """Re-create the view and list/get functions for a table.

    Also fixes any column-type drift between the stored field metadata and
    the actual database schema (e.g. multilingual fields that should be jsonb
    but are still text from a previous code version).

    Args:
        db: Active database connection.
        table_id: Primary key of the table.
        user: Email of the acting admin.

    Returns:
        Dict with 'regenerated', 'table', and 'altered_columns' keys.

    Raises:
        ValueError: If the table is not found.
    """
    tbl, flds = await load_table_with_fields(db, table_id)
    tbl_dict  = dict(tbl)

    # Fetch common_fields metadata for repair passes below.
    cf_repair_rows = await rows(
        db,
        """SELECT cf.code, cf.show_translation,
                  cf.true_label AS base_true_label, cf.false_label AS base_false_label,
                  cft.lang, cft.true_label AS tr_true_label, cft.false_label AS tr_false_label
           FROM toolkit.common_fields cf
           LEFT JOIN toolkit.common_fields_translation cft ON cft.common_field_id = cf.id
           WHERE cf.is_active = TRUE
           ORDER BY cf.code, cft.lang""",
    )
    _cf_repair_meta: dict       = {}
    _cf_repair_true: dict       = {}
    _cf_repair_false: dict      = {}
    _cf_multilingual_codes: set = set()
    for r in cf_repair_rows:
        c = r["code"]
        if c not in _cf_repair_meta:
            _cf_repair_meta[c] = r
            if r.get("show_translation"):
                _cf_multilingual_codes.add(c)
        if r.get("lang"):
            if r.get("tr_true_label"):
                _cf_repair_true.setdefault(c, {})[r["lang"]] = r["tr_true_label"]
            if r.get("tr_false_label"):
                _cf_repair_false.setdefault(c, {})[r["lang"]] = r["tr_false_label"]

    flds_list = [dict(f) for f in flds]
    for fld_dict in flds_list:
        fc  = safe_name(fld_dict.get("code", ""))
        cfg = dict(fld_dict.get("config") or {})

        # Repair 1: is_multilingual for common fields with show_translation=TRUE
        if fc in _cf_multilingual_codes and not fld_dict.get("is_multilingual"):
            await execute(
                db,
                "UPDATE toolkit_fields SET is_multilingual = TRUE WHERE id = $1",
                fld_dict["id"],
            )
            fld_dict["is_multilingual"] = True
            logger.info(
                "[regenerate] %s.%s: repaired is_multilingual=TRUE for %r",
                tbl_dict.get("schema_name"), tbl_dict["code"], fc,
            )

        # Repair 2: true_label_i18n / false_label_i18n for toggle common fields
        if fc in _cf_repair_meta and fld_dict.get("field_type") == "toggle":
            meta        = _cf_repair_meta[fc]
            base_true   = meta.get("base_true_label")
            base_false  = meta.get("base_false_label")
            true_i18n   = dict(_cf_repair_true.get(fc, {}))
            false_i18n  = dict(_cf_repair_false.get(fc, {}))
            if base_true:
                true_i18n.setdefault("en", base_true)
            if base_false:
                false_i18n.setdefault("en", base_false)
            changed = False
            if true_i18n and cfg.get("true_label_i18n") != true_i18n:
                cfg["true_label_i18n"]  = true_i18n
                cfg["true_label"]       = base_true or true_i18n.get("en", "")
                changed = True
            if false_i18n and cfg.get("false_label_i18n") != false_i18n:
                cfg["false_label_i18n"] = false_i18n
                cfg["false_label"]      = base_false or false_i18n.get("en", "")
                changed = True
            if changed:
                await execute(
                    db,
                    "UPDATE toolkit_fields SET config = $1 WHERE id = $2",
                    json.dumps(cfg), fld_dict["id"],
                )
                fld_dict["config"] = cfg
                logger.info(
                    "[regenerate] %s.%s: repaired toggle labels for %r",
                    tbl_dict.get("schema_name"), tbl_dict["code"], fc,
                )
    flds = flds_list

    # Fix column-type drift before rebuilding the view
    actual_types  = await get_actual_col_types(db, tbl_dict)
    altered: list[str] = []
    for fld in flds:
        fc      = safe_name(fld.get("code", ""))
        new_pg  = pg_type(dict(fld))
        old_pg  = actual_types.get(fc)
        if not fc or new_pg is None or old_pg is None:
            continue
        if old_pg != new_pg:
            logger.info(
                "[regenerate] %s.%s: altering column %r from %s → %s",
                tbl_dict.get("schema_name"), tbl_dict["code"], fc, old_pg, new_pg,
            )
            await exec_ddl(
                db, build_alter_column(tbl_dict, dict(fld)),
                tbl_dict["code"], "alter_column", user,
            )
            altered.append(fc)

    cols      = await get_actual_cols(db, tbl_dict)
    ref_map, schema_map = await fetch_ref_fields(db, flds)
    _schema = tbl_dict.get("schema_name", "public")
    _code   = tbl_dict["code"]
    custom_view     = await _custom_sql(db, _schema, f"v_{_code}",       "view")
    custom_list_fn  = await _custom_sql(db, _schema, f"fn_list_{_code}", "function")

    all_ops = [
        ("create_view",      "view",     custom_view    or build_create_view(tbl, flds, cols, ref_fields_map=ref_map, ref_schema_map=schema_map)),
        ("create_function",  "function", custom_list_fn or build_list_function(tbl)),
        ("write_functions",  "function", build_write_functions(tbl_dict, flds_list)),
    ]
    _ddl_log: list = []
    for op, otype, sql in all_ops:
        if object_type is None or object_type == otype:
            await exec_ddl(db, sql, tbl_dict["code"], op, user, _log_batch=_ddl_log)
    await flush_ddl_log(db, _ddl_log)

    await save_metadata(db, tbl["id"], _tbl=dict(tbl), _flds=list(flds))
    return {"regenerated": True, "table": tbl_dict["code"], "altered_columns": altered}


async def reset_object(
    db: asyncpg.Connection,
    table_id: int,
    object_code: str,
    object_type: str,
    user: str,
) -> dict:
    """Delete a user-customised view or function from toolkit_objects and
    re-apply the auto-generated version from the field catalog.

    Args:
        table_id:    Toolkit table primary key.
        object_code: DB object name without schema, e.g. 'v_brands', 'fn_list_brands'.
        object_type: 'view' or 'function'.
        user:        Acting admin email for the DDL log.

    Returns:
        {'sql': <regenerated SQL>}
    """
    tbl, flds = await load_table_with_fields(db, table_id)
    tbl_dict  = dict(tbl)
    schema    = tbl_dict.get("schema_name", "public")
    tbl_code  = tbl_dict["code"]

    # Remove custom version so future saves/regenerates use auto-generated SQL
    await db.execute(
        "DELETE FROM toolkit_objects "
        "WHERE schema_name=$1 AND code=$2 AND object_type=$3",
        schema, object_code, object_type,
    )

    cols                = await get_actual_cols(db, tbl_dict)
    ref_map, schema_map = await fetch_ref_fields(db, flds)

    if object_type == "view" and object_code == f"v_{tbl_code}":
        sql = build_create_view(tbl, flds, cols, ref_fields_map=ref_map, ref_schema_map=schema_map)
    elif object_type == "function" and object_code == f"fn_list_{tbl_code}":
        sql = build_list_function(tbl)
    else:
        raise ValueError(
            f"'{object_code}' ({object_type}) is not a standard auto-generated toolkit object — cannot reset."
        )

    await exec_ddl(db, sql, tbl_code, f"reset_{object_type}", user)
    return {"sql": sql}


async def regenerate_all_tables(db: asyncpg.Connection, user: str) -> dict:
    """Re-create view + all function wrappers for every active toolkit table."""
    all_tables = await rows(
        db,
        "SELECT id FROM toolkit.toolkit_tables WHERE is_active = TRUE ORDER BY schema_name, code",
    )
    result: dict = {"success": [], "failed": []}
    for row_data in all_tables:
        try:
            regen = await regenerate_view(db, row_data["id"], user)
            result["success"].append(regen.get("table", str(row_data["id"])))
        except Exception as exc:
            logger.exception("regenerate_all: failed for table id=%s", row_data["id"])
            result["failed"].append({"id": row_data["id"], "error": str(exc)})
    return result
