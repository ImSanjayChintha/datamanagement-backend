"""
Gateway runtime executor — clean, robust, translatable.

Builds and runs parameterised SQL for all HTTP methods.

Translation model
-----------------
Multilingual fields (column.is_multilingual = true) are stored in a translations
table co-located with the main table in the SAME schema:

    {db_schema}.translations (table_name, entity_id, field_code, lang_code, value)

The gateway DB role only needs access to the target schema — zero cross-schema deps.
The PK column used as entity_id is configurable via endpoint.config.pk_column
(default: "id").  Override the translation table via endpoint.config:
    {
      "pk_column": "id",
      "translation_schema": null,   # defaults to db_schema
      "translation_table": "translations"
    }

Security
--------
• Column names are whitelisted against endpoint definition before use in SQL.
• Operator names are whitelisted per column's allowed set.
• ALL values go through asyncpg $n parameters — never string-interpolated.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg

from app.modules.api_bridge.gateway.db.setup import create_schema_translations

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security — identifier validation
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]{0,62}$')


def _validate_identifier(name: str, context: str = "identifier") -> str:
    """Reject any name that could bypass double-quote escaping in SQL."""
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(
            f"Invalid {context} '{name}': must start with a letter/underscore "
            f"and contain only [A-Za-z0-9_$] (max 63 chars)."
        )
    return name


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OP_SQL: dict[str, str] = {
    "eq":      '"{col}" = ${n}',
    "ne":      '"{col}" != ${n}',
    "lt":      '"{col}" < ${n}',
    "lte":     '"{col}" <= ${n}',
    "gt":      '"{col}" > ${n}',
    "gte":     '"{col}" >= ${n}',
    "like":    '"{col}" LIKE ${n}',
    "ilike":   '"{col}" ILIKE ${n}',
    "in":      '"{col}" = ANY(${n})',
    "is_null": '"{col}" IS NULL',
}
_NO_PARAM_OPS = {"is_null"}

_METHOD_DEFAULT_OP: dict[str, str] = {
    "GET":    "select",
    "POST":   "insert",
    "PUT":    "update",
    "PATCH":  "update",
    "DELETE": "delete",
}

# Parameters that carry JSONB values and need json.dumps + ::jsonb cast
_JSONB_PARAMS: frozenset[str] = frozenset({"p_filters", "p_sort", "p_id"})

# ---------------------------------------------------------------------------
# Endpoint parsing helpers
# ---------------------------------------------------------------------------

def _parse_jsonb(value: Any, default: Any) -> Any:
    """asyncpg returns JSONB columns as plain Python objects or strings."""
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return default


def parse_endpoint(row: Any) -> dict:
    """Convert a raw asyncpg Row from api_gateway.endpoints to a plain dict."""
    d = dict(row)
    d["headers"]     = _parse_jsonb(d.get("headers"),     [])
    d["body_schema"] = _parse_jsonb(d.get("body_schema"), {})
    d["filters"]     = _parse_jsonb(d.get("filters"),     [])
    d["config"]      = _parse_jsonb(d.get("config"),      {})
    # columns may be stored as ["col1", "col2"] (names only) or [{"name": "col1", ...}]
    raw = _parse_jsonb(d.get("columns"), [])
    d["columns"] = [
        c if isinstance(c, dict) else {"name": c, "type": "text", "nullable": True}
        for c in raw
    ]
    return d


def _get_config(endpoint: dict) -> dict:
    """Return resolved endpoint config with defaults applied."""
    cfg = endpoint.get("config") or {}
    return {
        "pk_column":          _validate_identifier((cfg.get("pk_column") or "id").strip(), "pk_column"),
        "translation_schema": _validate_identifier((cfg.get("translation_schema") or endpoint.get("db_schema") or "public").strip(), "translation_schema"),
        "translation_table":  _validate_identifier((cfg.get("translation_table") or "translations").strip(), "translation_table"),
    }


def effective_op(endpoint: dict) -> str:
    """Resolve the SQL operation: explicit operation_type wins, else infer from method."""
    return endpoint.get("operation_type") or _METHOD_DEFAULT_OP.get(endpoint["method"], "select")


def _ml_cols(endpoint: dict) -> set[str]:
    """Return the set of column names marked as multilingual on this endpoint."""
    return {c["name"] for c in (endpoint.get("columns") or []) if c.get("is_multilingual")}


# ---------------------------------------------------------------------------
# Path template matching  /customers/{id} → regex + param names
# ---------------------------------------------------------------------------

def _template_to_regex(template: str) -> tuple[re.Pattern, list[str]]:
    param_names = re.findall(r"\{(\w+)\}", template)
    escaped     = re.escape(template)
    regex_str   = re.sub(r"\\\{(\w+)\\\}", r"([^/]+)", escaped)
    return re.compile(f"^{regex_str}$"), param_names


async def load_endpoint(
    db: asyncpg.Connection,
    url_path: str,
    method: str,
) -> dict | None:
    """
    Fetch one active endpoint by url_path + method.

    Matching order:
      1. Exact string match   (fastest path)
      2. Template match       url_path contains {param} placeholders
         Extracted values are stored in endpoint["_path_params"].
    """
    method = method.upper()

    row = await db.fetchrow(
        "SELECT * FROM api_gateway.endpoints "
        "WHERE url_path = $1 AND method = $2 AND status = 'active'",
        url_path, method,
    )
    if row:
        ep = parse_endpoint(row)
        ep["_path_params"] = {}
        return ep

    # Template match — only rows that contain a {param} placeholder
    rows = await db.fetch(
        "SELECT * FROM api_gateway.endpoints "
        "WHERE method = $1 AND status = 'active' AND url_path LIKE '%{%'",
        method,
    )
    for row in rows:
        pattern, param_names = _template_to_regex(row["url_path"])
        m = pattern.match(url_path)
        if m:
            ep = parse_endpoint(row)
            ep["_path_params"] = dict(zip(param_names, m.groups()))
            return ep

    return None


# ---------------------------------------------------------------------------
# Filter injection — path params become implicit eq filters
# ---------------------------------------------------------------------------

def _merge_path_params(filters: dict[str, Any], endpoint: dict) -> dict[str, Any]:
    """
    Inject URL path params into the filter dict as implicit `eq` conditions.
    The merged dict is a new object — the original is not mutated.
    """
    path_params: dict[str, str] = endpoint.get("_path_params") or {}
    if not path_params:
        return filters
    merged = dict(filters)
    for name, value in path_params.items():
        merged.setdefault(name, value)
        merged.setdefault(f"{name}__op", "eq")
    return merged


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------

def _build_col_list(col_defs: list[dict]) -> str:
    """
    Build a quoted SELECT/RETURNING column list.
    Falls back to * when no columns are configured.
    Applies alias: "col" AS "alias" when alias differs from name.
    """
    if not col_defs:
        return "*"
    parts: list[str] = []
    for c in col_defs:
        name  = _validate_identifier(c["name"], "column name")
        alias = (c.get("alias") or "").strip()
        if alias and alias != name:
            alias = _validate_identifier(alias, "column alias")
            parts.append(f'"{name}" AS "{alias}"')
        else:
            parts.append(f'"{name}"')
    return ", ".join(parts)


def _build_where(
    filter_defs: list[dict],
    filter_input: dict[str, Any],
    params: list[Any],
) -> list[str]:
    """
    Translate runtime filter values into SQL WHERE fragments.

    filter_defs   — endpoint["filters"]:  [{column, operators}, ...]
    filter_input  — request filters dict: {col: val, col__op: op, ...}

    Column names come from the endpoint whitelist — never from user input.
    Operator names are validated against each column's allowed set.
    Values go through $n parameters — never interpolated.
    """
    parts: list[str] = []

    for fdef in filter_defs:
        col     = _validate_identifier(fdef["column"], "filter column")
        allowed = fdef.get("operators") or ["eq"]

        if col not in filter_input and f"{col}__op" not in filter_input:
            continue  # filter not provided — skip

        op = filter_input.get(f"{col}__op") or (allowed[0] if allowed else "eq")

        if op not in _OP_SQL:
            raise ValueError(f"Unknown operator '{op}' for column '{col}'.")
        if allowed and op not in allowed:
            raise ValueError(
                f"Operator '{op}' is not allowed for column '{col}'. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )

        if op in _NO_PARAM_OPS:
            parts.append(f'"{col}" IS NULL')

        elif op == "in":
            val = filter_input.get(col, [])
            if not isinstance(val, list):
                val = [val]
            params.append(val)
            parts.append(f'"{col}" = ANY(${len(params)})')

        else:
            val = filter_input.get(col)
            params.append(val)
            parts.append(
                _OP_SQL[op]
                .replace("{col}", col)
                .replace("{n}", str(len(params)))
            )

    return parts


# ---------------------------------------------------------------------------
# Translation helper
# ---------------------------------------------------------------------------

async def _upsert_translations(
    db: asyncpg.Connection,
    schema: str,
    table: str,
    table_name: str,
    entity_id: Any,
    translations: dict[str, dict[str, Any]],
    ml_cols: set[str],
) -> None:
    """
    Upsert / delete rows in {schema}.{table} for multilingual fields.

    translations — {"en": {"name": "Foo"}, "es": {"name": "Foo ES"}}
    ml_cols      — column names whitelisted as multilingual on this endpoint

    Empty string or None → DELETE that translation row.
    """
    await create_schema_translations(db, schema)

    schema = _validate_identifier(schema, "translation_schema")
    table  = _validate_identifier(table, "translation_table")
    tbl    = f'"{schema}"."{table}"'

    for lang_code, lang_data in translations.items():
        if not isinstance(lang_data, dict):
            continue
        for field_code, value in lang_data.items():
            if field_code not in ml_cols:
                continue  # only whitelisted multilingual columns
            if value is None or value == "":
                await db.execute(
                    f"DELETE FROM {tbl} "
                    "WHERE table_name=$1 AND entity_id=$2 AND field_code=$3 AND lang_code=$4",
                    table_name, entity_id, field_code, lang_code,
                )
            else:
                await db.execute(
                    f"INSERT INTO {tbl} (table_name, entity_id, field_code, lang_code, value) "
                    "VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (table_name, entity_id, field_code, lang_code) "
                    "DO UPDATE SET value = EXCLUDED.value, modified_at = NOW()",
                    table_name, entity_id, field_code, lang_code, str(value),
                )


# ---------------------------------------------------------------------------
# Execute: GET (SELECT)
# ---------------------------------------------------------------------------

async def execute_get(
    db: asyncpg.Connection,
    endpoint: dict,
    filters: dict[str, Any],
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    SELECT <columns> FROM <schema>.<table|view|function>
     WHERE <filter conditions>
     LIMIT $n OFFSET $m
    """
    schema      = _validate_identifier(endpoint["db_schema"], "schema")
    obj         = _validate_identifier(endpoint["db_object"], "object")
    db_type     = endpoint.get("db_type") or "table"
    col_defs    = endpoint.get("columns") or []
    filter_defs = endpoint.get("filters") or []

    params:  list[Any] = []
    filters = _merge_path_params(filters, endpoint)
    select  = _build_col_list(col_defs)

    if db_type == "function":
        # body_schema keys define the function input parameters.
        # Use named-parameter syntax (param => $n) so JSONB alphabetical key
        # ordering never misaligns values with function positional parameters.
        func_schema: dict = endpoint.get("body_schema") or {}
        for k in func_schema:
            _validate_identifier(k, "function parameter")
        func_items  = [(k, filters.get(k)) for k in func_schema if filters.get(k) is not None]

        if func_items:
            params.extend(v for _, v in func_items)
            base = len(params) - len(func_items)
            placeholders = ", ".join(
                f"{k} => ${base + i + 1}" for i, (k, _) in enumerate(func_items)
            )
            from_clause = f'"{schema}"."{obj}"({placeholders})'
        else:
            from_clause = f'"{schema}"."{obj}"()'

        # Post-call WHERE: filters that are NOT function input params
        func_param_names = set(func_schema)
        post_defs   = [f for f in filter_defs if f["column"] not in func_param_names]
        where_parts = _build_where(post_defs, filters, params)
    else:
        from_clause = f'"{schema}"."{obj}"'
        where_parts = _build_where(filter_defs, filters, params)

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    params += [limit, offset]
    sql = (
        f'SELECT {select}\n'
        f'  FROM {from_clause}\n'
        f'  {where_clause}\n'
        f' LIMIT ${len(params) - 1} OFFSET ${len(params)}'
    )

    logger.debug(
        "execute_get — db_type=%s schema=%s obj=%s op=%s\nSQL: %s\nPARAMS: %s",
        db_type, schema, obj, endpoint.get("operation_type"), sql, params,
    )
    rows = await db.fetch(sql, *params)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Execute: POST (INSERT)
# ---------------------------------------------------------------------------

async def execute_post(
    db: asyncpg.Connection,
    endpoint: dict,
    data: dict[str, Any],
    translations: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """
    INSERT INTO <schema>.<table> (col1, col2, ...)
    VALUES ($1, $2, ...)
    RETURNING <columns>

    When multilingual columns exist and `translations` is provided, upserts
    into {translation_schema}.translations within the same transaction.
    """
    schema   = _validate_identifier(endpoint["db_schema"], "schema")
    table    = _validate_identifier(endpoint["db_object"], "table")
    col_defs = endpoint.get("columns") or []

    if not col_defs:
        raise ValueError("Endpoint has no column definitions; cannot validate insert data.")

    allowed_cols: set[str] = {c["name"] for c in col_defs}
    ml = _ml_cols(endpoint)

    insert_cols: list[str] = []
    params:      list[Any] = []

    for col, val in data.items():
        if col in ml:
            continue  # multilingual fields go to translations table, not main table
        if col not in allowed_cols:
            raise ValueError(f"Column '{col}' is not in the endpoint's allowed column list.")
        insert_cols.append(f'"{col}"')
        params.append(val)

    if not insert_cols:
        raise ValueError("'data' must contain at least one column.")

    placeholders = ", ".join(f"${i + 1}" for i in range(len(params)))
    returning    = _build_col_list(col_defs)

    sql = (
        f'INSERT INTO "{schema}"."{table}" ({", ".join(insert_cols)})\n'
        f'VALUES ({placeholders})\n'
        f'RETURNING {returning}'
    )

    async with db.transaction():
        row    = await db.fetchrow(sql, *params)
        result = dict(row) if row else {}

        if translations and result and ml:
            cfg       = _get_config(endpoint)
            entity_id = result.get(cfg["pk_column"])
            if entity_id is None:
                raise ValueError(
                    f"Cannot save translations: pk_column '{cfg['pk_column']}' "
                    f"not found in RETURNING result. Check endpoint config."
                )
            await _upsert_translations(
                db,
                cfg["translation_schema"],
                cfg["translation_table"],
                table,
                entity_id,
                translations,
                ml,
            )

    return result


# ---------------------------------------------------------------------------
# Execute: PATCH (UPDATE)
# ---------------------------------------------------------------------------

async def execute_patch(
    db: asyncpg.Connection,
    endpoint: dict,
    patch_data: dict[str, Any],
    filters: dict[str, Any],
    translations: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    """
    UPDATE <schema>.<table>
       SET  col=$1, ...
     WHERE  <filter conditions>
    RETURNING <columns>

    Refuses to run without at least one filter (prevents accidental full-table updates).
    Upserts translations for each updated row within the same transaction.
    """
    schema      = _validate_identifier(endpoint["db_schema"], "schema")
    table       = _validate_identifier(endpoint["db_object"], "table")
    col_defs    = endpoint.get("columns") or []
    filter_defs = endpoint.get("filters") or []
    ml          = _ml_cols(endpoint)

    if not col_defs:
        raise ValueError("Endpoint has no column definitions; cannot validate update data.")

    allowed_cols: set[str] = {c["name"] for c in col_defs}
    params:  list[Any] = []
    filters = _merge_path_params(filters, endpoint)

    # ── SET clause ──
    set_parts: list[str] = []
    for col, val in patch_data.items():
        if col in ml:
            continue  # multilingual fields go to translations table
        if col not in allowed_cols:
            raise ValueError(f"Column '{col}' is not in the endpoint's allowed column list.")
        params.append(val)
        set_parts.append(f'"{col}" = ${len(params)}')

    if not set_parts:
        raise ValueError("'patch_data' must contain at least one non-multilingual column.")

    # ── WHERE clause ──
    where_parts = _build_where(filter_defs, filters, params)
    if not where_parts:
        raise ValueError(
            "At least one filter is required for UPDATE to prevent full-table modifications."
        )

    returning = _build_col_list(col_defs)
    sql = (
        f'UPDATE "{schema}"."{table}"\n'
        f'   SET {", ".join(set_parts)}\n'
        f' WHERE {" AND ".join(where_parts)}\n'
        f'RETURNING {returning}'
    )

    async with db.transaction():
        rows   = await db.fetch(sql, *params)
        result = [dict(r) for r in rows]

        if translations and result and ml:
            cfg        = _get_config(endpoint)
            pk_col     = cfg["pk_column"]
            for row_dict in result:
                entity_id = row_dict.get(pk_col)
                if entity_id is not None:
                    await _upsert_translations(
                        db,
                        cfg["translation_schema"],
                        cfg["translation_table"],
                        table,
                        entity_id,
                        translations,
                        ml,
                    )

    return result


# ---------------------------------------------------------------------------
# Execute: DELETE
# ---------------------------------------------------------------------------

async def execute_delete(
    db: asyncpg.Connection,
    endpoint: dict,
    filters: dict[str, Any],
) -> int:
    """
    DELETE FROM <schema>.<table> WHERE <filter conditions>
    Returns the number of deleted rows.

    Refuses to run without at least one filter (prevents full-table deletes).
    """
    schema      = _validate_identifier(endpoint["db_schema"], "schema")
    table       = _validate_identifier(endpoint["db_object"], "table")
    filter_defs = endpoint.get("filters") or []

    params:  list[Any] = []
    filters = _merge_path_params(filters, endpoint)
    where_parts = _build_where(filter_defs, filters, params)

    if not where_parts:
        raise ValueError(
            "At least one filter is required for DELETE to prevent full-table deletions."
        )

    sql = (
        f'DELETE FROM "{schema}"."{table}"\n'
        f' WHERE {" AND ".join(where_parts)}'
    )

    result = await db.execute(sql, *params)
    return int(result.split()[-1])  # asyncpg returns "DELETE N"


# ---------------------------------------------------------------------------
# Execute: scalar-JSONB function  (config.returns == "jsonb")
# ---------------------------------------------------------------------------

async def call_jsonb_function(
    db:       asyncpg.Connection,
    endpoint: dict,
    body:     dict,
) -> Any:
    """Call a PG function that RETURNS jsonb and hand its output back verbatim.

    The function does its own filtering, sorting, pagination and counting — no
    column projection and no WHERE / LIMIT / OFFSET are wrapped around the call.
    Binding is driven by endpoint.body_schema; only declared params are sent.
    None values are omitted so the SQL default applies rather than an explicit null.
    Explicit ::jsonb casts are emitted for JSONB params so asyncpg never passes a
    text literal that Postgres cannot resolve against the jsonb parameter type.
    """
    schema  = _validate_identifier(endpoint["db_schema"], "schema")
    fn_name = _validate_identifier(endpoint["db_object"], "function")
    sig     = endpoint.get("body_schema") or {}
    cfg     = endpoint.get("config") or {}

    max_limit = int(cfg.get("max_limit") or 200)
    default_l = int(cfg.get("default_limit") or 25)
    req_limit = body.get("limit")
    eff_limit = min(int(req_limit) if req_limit is not None else default_l, max_limit)

    candidates: dict[str, Any] = {
        "p_filters":    body.get("filters"),   # None → skip so SQL DEFAULT applies
        "p_sort":       body.get("sort"),
        "p_lang":       body.get("lang") or "en",
        "p_limit":      eff_limit,
        "p_offset":     int(body.get("offset") or 0),
        "p_with_total": bool(cfg.get("include_total", True)),
        "p_id":         body.get("id"),
    }

    params: list[Any] = []
    binds:  list[str] = []
    for key in sig:
        _validate_identifier(key, "function parameter")
        if key not in candidates:
            continue
        val = candidates[key]
        if val is None:
            continue
        if key in _JSONB_PARAMS:
            params.append(json.dumps(val))
            binds.append(f"{key} => ${len(params)}::jsonb")
        else:
            params.append(val)
            binds.append(f"{key} => ${len(params)}")

    sql = f'SELECT "{schema}"."{fn_name}"({", ".join(binds)}) AS result'
    logger.debug("jsonb-fn %s.%s  params=%s\nSQL: %s", schema, fn_name, params, sql)

    row = await db.fetchrow(sql, *params)
    result = row["result"] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    return result


# ---------------------------------------------------------------------------
# Execute: Function write (INSERT / UPDATE / SYNC / UPSERT / DELETE via PG fn)
# ---------------------------------------------------------------------------

async def execute_function_write(
    db:         asyncpg.Connection,
    endpoint:   dict,
    body:       dict,
    user_email: str | None = None,
) -> dict:
    """Call a write-capable PG function registered as this endpoint's db_object.

    For insert / update / sync / upsert  → fn(p_data   jsonb, p_audit_user text) → jsonb
    For delete                           → fn(p_filter jsonb, p_audit_user text) → jsonb

    The function must be created in db_schema with the matching signature.
    """
    schema  = _validate_identifier(endpoint["db_schema"], "schema")
    fn_name = _validate_identifier(endpoint["db_object"], "function")
    op      = effective_op(endpoint)

    if op == "delete":
        payload    = body.get("filters") or {}
        param_name = "p_filter"
    else:
        raw        = body.get("data") or []
        payload    = raw if isinstance(raw, list) else [raw]
        param_name = "p_data"

    sql = f'SELECT "{schema}"."{fn_name}"({param_name} => $1::jsonb, p_audit_user => $2) AS result'
    row = await db.fetchrow(sql, json.dumps(payload), user_email)
    if not row:
        return {"ok": True}

    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    return result if isinstance(result, dict) else {"ok": True, "result": result}
