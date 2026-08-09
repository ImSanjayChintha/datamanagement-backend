"""SQL query builders for the API engine.

All identifiers (schema, table, column names) are sourced from the ObjectMeta /
FieldMeta registry — never from request bodies.  All values are bound as $N
parameters via the shared ParamBag.  Every query prefixes the main table with
alias 't' for unambiguous column references when joins are present.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .errors import EngineError, unknown_field
from .filters import ParamBag
from .registry import ObjectMeta, FieldMeta, I18N_TYPES


# ── Select field resolution ───────────────────────────────────────────────────

def resolve_select(
    obj:         ObjectMeta,
    select:      list[str] | None,
    hidden:      set[str],
    include_pk:  bool = True,
) -> list[FieldMeta]:
    """Return the ordered list of FieldMeta to include in SELECT.

    If select is None, uses all in-list fields that are not hidden.
    If select is provided, whitelists each name against the registry.
    """
    if not select:
        return [f for f in obj.fields if f.in_list and f.field_name not in hidden]

    result: list[FieldMeta] = []
    for name in select:
        if name in hidden:
            raise unknown_field(name)
        f = obj.fields_by_name.get(name)
        if f is None:
            raise unknown_field(name)
        result.append(f)

    if include_pk and obj.pk_field:
        pk = obj.fields_by_name.get(obj.pk_field)
        if pk and pk not in result:
            result.insert(0, pk)

    return result


# ── Column expression helpers ─────────────────────────────────────────────────

def _col_expr(
    field:    FieldMeta,
    locale:   str | None,
    fallback: str,
    raw_i18n: bool,
) -> str:
    """SELECT expression for a single field (main table alias is always 't')."""
    qualified = f't."{field.field_name}"'
    if field.field_type in I18N_TYPES and locale and not raw_i18n:
        safe_want = locale.replace("'", "")
        safe_fb   = fallback.replace("'", "")
        return f"pim.tr({qualified}, '{safe_want}', '{safe_fb}') AS \"{field.field_name}\""
    return f'{qualified} AS "{field.field_name}"'


# ── Sort compilation ──────────────────────────────────────────────────────────

def compile_sort(
    sort:           list[dict],
    fields_by_name: dict,
    locale:         str | None,
) -> str:
    """Compile a sort spec list into an ORDER BY fragment.

    Falls back to sort_order / code / id when no sort is specified.
    """
    if not sort:
        defaults = []
        if "sort_order" in fields_by_name:
            defaults.append('t."sort_order" ASC')
        if "code" in fields_by_name:
            defaults.append('t."code" ASC')
        return ", ".join(defaults) or 't."id" ASC'

    parts = []
    for item in sort:
        fname = item.get("field")
        if not fname or fname not in fields_by_name:
            raise unknown_field(fname or "(missing)")

        f = fields_by_name[fname]
        direction = item.get("dir", "asc").upper()
        if direction not in ("ASC", "DESC"):
            raise EngineError("validation_failed", f"Invalid sort direction: {direction!r}")

        item_locale = item.get("locale", locale)
        if f.field_type in I18N_TYPES and item_locale:
            safe_l = item_locale.replace("'", "")
            col = f'pim.tr(t."{f.field_name}", \'{safe_l}\')'
        else:
            col = f't."{f.field_name}"'

        parts.append(f"{col} {direction}")

    return ", ".join(parts)


# ── LIST query ────────────────────────────────────────────────────────────────

def build_list_sql(
    obj:           ObjectMeta,
    select_fields: list[FieldMeta],
    filter_sql:    str | None,
    sort_sql:      str,
    skip:          int,
    limit:         int,
    params:        ParamBag,
    expand:        list[str],
    locale:        str | None,
    fallback:      str,
    raw_i18n:      bool,
    include_window_count: bool = False,
) -> str:
    col_exprs: list[str] = [
        _col_expr(f, locale, fallback, raw_i18n) for f in select_fields
    ]
    if include_window_count:
        # First column — computed before LIMIT/OFFSET, counts all rows matching WHERE
        col_exprs.insert(0, "COUNT(*) OVER() AS total_rows")

    join_parts: list[str] = []
    for field_name in expand:
        f = obj.fields_by_name.get(field_name)
        if not f or not f.ref:
            continue
        parts = f.ref.get("object", "").split(".")
        if len(parts) != 2:
            continue
        ref_schema, ref_table = parts
        ref_val   = f.ref["value_field"]
        ref_label = f.ref["label_field"]
        alias     = f"_x_{field_name}"

        join_parts.append(
            f'LEFT JOIN "{ref_schema}"."{ref_table}" {alias}'
            f' ON {alias}."{ref_val}" = t."{field_name}"'
        )
        if locale:
            safe_l = locale.replace("'", "")
            label_expr = f'pim.tr({alias}."{ref_label}", \'{safe_l}\')'
        else:
            label_expr = f'{alias}."{ref_label}"'
        col_exprs.append(f'{label_expr} AS "{field_name}_label"')

    from_sql = f"{_from(obj)} t"
    join_sql = " ".join(join_parts)
    where_sql = f"WHERE {filter_sql}" if filter_sql else ""

    p_limit = params.add(limit)
    p_skip  = params.add(skip)

    return (
        f"SELECT {', '.join(col_exprs)}"
        f" FROM {from_sql}"
        f" {join_sql}"
        f" {where_sql}"
        f" ORDER BY {sort_sql}"
        f" LIMIT {p_limit} OFFSET {p_skip}"
    )


def _from(obj: ObjectMeta) -> str:
    """Return the FROM target — functions need () to be called as set-returning."""
    tbl = f'"{obj.schema_name}"."{obj.object_name}"'
    return f"{tbl}()" if obj.kind == "function" else tbl


def build_count_sql(obj: ObjectMeta, filter_sql: str | None) -> str:
    where_sql = f"WHERE {filter_sql}" if filter_sql else ""
    return f"SELECT count(*) FROM {_from(obj)} t {where_sql}"


def build_bounded_count_sql(obj: ObjectMeta, filter_sql: str | None, cap: int) -> str:
    """Count rows but stop scanning after cap+1."""
    where_sql = f"WHERE {filter_sql}" if filter_sql else ""
    return (
        f"SELECT count(*) FROM ("
        f"  SELECT 1 FROM {_from(obj)} t {where_sql} LIMIT {cap + 1}"
        f") _count_cap"
    )


# ── INSERT ────────────────────────────────────────────────────────────────────

def build_insert_sql(
    obj:        ObjectMeta,
    data:       dict,
    params:     ParamBag,
    audit_user: str | None,
) -> str:
    """Build a parameterized INSERT … RETURNING * statement.

    Audit columns (inserted_at/by, modified_at/by) are appended automatically
    when they are present in the registry as readonly fields.
    """
    col_names: list[str] = []
    col_vals:  list[str] = []

    for field_name, value in data.items():
        col_names.append(f'"{field_name}"')
        col_vals.append(params.add(value))

    now = datetime.now(timezone.utc)
    audit_map = {
        "inserted_at": now,
        "modified_at": now,
        "inserted_by": audit_user,
        "modified_by": audit_user,
    }
    for col, val in audit_map.items():
        f = obj.fields_by_name.get(col)
        if f and f.is_readonly:
            col_names.append(f'"{col}"')
            col_vals.append(params.add(val))

    tbl = f'"{obj.schema_name}"."{obj.object_name}"'
    return (
        f"INSERT INTO {tbl} ({', '.join(col_names)})"
        f" VALUES ({', '.join(col_vals)})"
        f" RETURNING *"
    )


def build_bulk_insert_sql(
    obj:        ObjectMeta,
    rows:       list[dict],
    params:     ParamBag,
    audit_user: str | None,
) -> str:
    """Multi-row VALUES INSERT for bulk operations.

    Computes the column union across all rows so sparse rows (different
    callers omitting optional fields) are handled correctly — missing values
    become NULL.  Audit columns injected from the registry.

    Caller must ensure len(rows) > 0.
    """
    if not rows:
        raise EngineError("validation_failed", "No rows to insert")

    # Union of all user-supplied column names (first-seen order)
    seen:      set[str]  = set()
    data_cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                data_cols.append(k)

    # Audit columns present and readonly in the registry
    now = datetime.now(timezone.utc)
    audit_map: dict[str, Any] = {
        "inserted_at": now,
        "modified_at": now,
        "inserted_by": audit_user,
        "modified_by": audit_user,
    }
    audit_cols: list[tuple[str, Any]] = [
        (col, val)
        for col, val in audit_map.items()
        if col in obj.fields_by_name and obj.fields_by_name[col].is_readonly
    ]

    all_col_names = [f'"{c}"' for c in data_cols] + [f'"{c}"' for c, _ in audit_cols]

    # Build one VALUES row per input row
    value_rows: list[str] = []
    for row in rows:
        placeholders = [params.add(row.get(c)) for c in data_cols]
        placeholders += [params.add(v) for _, v in audit_cols]
        value_rows.append(f"({', '.join(placeholders)})")

    tbl = f'"{obj.schema_name}"."{obj.object_name}"'
    pk  = obj.pk_field or "id"
    return (
        f"INSERT INTO {tbl}"
        f" ({', '.join(all_col_names)})"
        f" VALUES {', '.join(value_rows)}"
        f' RETURNING "{pk}"'
    )


# ── UPDATE ────────────────────────────────────────────────────────────────────

def build_update_sql(
    obj:        ObjectMeta,
    data:       dict,
    filter_sql: str,
    params:     ParamBag,
    audit_user: str | None,
) -> str:
    """Build a parameterized UPDATE … WHERE … RETURNING * statement.

    Supports field_name~merge suffix for JSONB || merge on i18n columns.
    """
    set_parts: list[str] = []

    for key, value in data.items():
        is_merge   = key.endswith("~merge")
        field_name = key[:-6] if is_merge else key
        f          = obj.fields_by_name.get(field_name)
        col        = f'"{field_name}"'
        p          = params.add(value)

        if is_merge and f and f.field_type in I18N_TYPES:
            set_parts.append(f"{col} = {col} || {p}::jsonb")
        else:
            set_parts.append(f"{col} = {p}")

    now = datetime.now(timezone.utc)
    for col, val in [("modified_at", now), ("modified_by", audit_user)]:
        f = obj.fields_by_name.get(col)
        if f and f.is_readonly:
            set_parts.append(f'"{col}" = {params.add(val)}')

    tbl = f'"{obj.schema_name}"."{obj.object_name}"'
    return (
        f"UPDATE {tbl} t SET {', '.join(set_parts)}"
        f" WHERE {filter_sql}"
        f" RETURNING *"
    )


# ── DELETE ────────────────────────────────────────────────────────────────────

def build_delete_sql(obj: ObjectMeta, filter_sql: str, soft: bool) -> str:
    tbl = f'"{obj.schema_name}"."{obj.object_name}"'
    if soft:
        return f'UPDATE {tbl} t SET "is_active" = FALSE WHERE {filter_sql}'
    return f'DELETE FROM {tbl} t WHERE {filter_sql}'
