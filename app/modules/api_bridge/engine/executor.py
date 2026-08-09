"""Execution pipeline for the API engine.

execute_action() is the single entry point — it resolves the object, checks
roles, dispatches to the action-specific handler, and returns the response dict.
"""
from __future__ import annotations

import logging

import asyncpg

from .compiler import (
    build_bounded_count_sql, build_bulk_insert_sql, build_count_sql,
    build_delete_sql, build_insert_sql, build_list_sql, build_update_sql,
    compile_sort, resolve_select,
)
from .errors import (
    EngineError, expected_mismatch, filter_required, limit_exceeded,
    not_found, not_unique, unknown_action, unknown_object,
    forbidden,
)
from .filters import ParamBag, compile_filter
from .i18n import resolve_locale_chain
from .registry import ObjectMeta, I18N_TYPES, registry
from .validators import validate_insert_data, validate_update_data

logger = logging.getLogger(__name__)

import asyncpg as _asyncpg

# PostgreSQL error code → EngineError mapping
_PG_CODE_MAP: dict[str, tuple[str, str]] = {
    "23505": ("conflict",           "Duplicate value — {detail}"),
    "23503": ("ref_not_found",      "Referenced record not found — {detail}"),
    "23502": ("required_field",     "Field is required — {detail}"),
    "23514": ("constraint_failed",  "Check constraint failed — {detail}"),
    "22P02": ("type_error",         "Invalid value format — {detail}"),
    "42703": ("unknown_field",      "Unknown column — {detail}"),
    "42P01": ("unknown_object",     "Table or view not found — {detail}"),
}

def _pg_error(exc: Exception) -> EngineError:
    """Translate a PostgresError into a structured EngineError.

    Falls through to EngineError("internal") for unknown error codes.
    """
    if isinstance(exc, EngineError):
        return exc
    if isinstance(exc, _asyncpg.PostgresError):
        code   = getattr(exc, "sqlstate", "") or ""
        detail = getattr(exc, "detail",   "") or str(exc)
        if code in _PG_CODE_MAP:
            err_code, tmpl = _PG_CODE_MAP[code]
            return EngineError(err_code, tmpl.format(detail=detail))
    return EngineError("internal", str(exc))


async def execute_action(
    schema:      str,
    object_name: str,
    action:      str,
    body:        dict,
    db:          asyncpg.Connection,
    user_email:  str | None,
    user_roles:  list[str],
) -> dict:
    """Resolve object, check roles, and dispatch to the action handler."""
    code = f"{schema}.{object_name}"

    obj = await registry.get(code, db)
    if obj is None:
        raise unknown_object(code)

    action_def = obj.action_def(action)
    if action_def is None:
        raise unknown_action(action)

    allowed = obj.allowed_roles(action)
    if not _role_allowed(user_roles, allowed):
        raise forbidden(f"Role not permitted for '{action}' on '{code}'")

    impl = action_def.get("impl", "generic")

    if impl == "function":
        fn_name = action_def.get("function")
        if not fn_name:
            raise EngineError("internal", f"Action '{action}' declares impl=function but no 'function' key")

        if action in ("insert", "upsert", "sync", "update"):
            from .functions import execute_write_function
            raw = body.get("data")
            payload: list | dict = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
            return await execute_write_function(fn_name, payload, db, user_email)

        if action == "delete":
            from .functions import execute_write_function
            return await execute_write_function(
                fn_name, body.get("filter") or {}, db, user_email, param_name="p_filter"
            )

        # list / get / custom read-only functions
        from .functions import execute_function
        return await execute_function(fn_name, body, db, user_email)

    # Generic CRUD dispatch
    handlers = {
        "list":   _list,
        "get":    _get,
        "insert": _insert,
        "update": _update,
        "delete": _delete,
    }
    handler = handlers.get(action)
    if handler is None:
        raise unknown_action(action)

    return await handler(obj, body, db, user_email)


# ── Role check ────────────────────────────────────────────────────────────────

def _role_allowed(user_roles: list[str], allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return bool(set(user_roles) & set(allowed))


# ── Action handlers ───────────────────────────────────────────────────────────

async def _list(obj: ObjectMeta, body: dict, db: asyncpg.Connection, user_email: str | None) -> dict:
    locale   = body.get("locale")
    raw_i18n = bool(body.get("raw_i18n", False))
    want_count        = bool(body.get("count", False))
    want_window_count = bool(body.get("count_window", False))
    hidden   = obj.hidden_fields()

    fallback = "en"
    if locale:
        _, fallback = await resolve_locale_chain(locale, db)

    # Pagination — endpoint config overrides object-level policy when present
    ep_default_limit = body.get("_ep_default_limit")
    ep_max_limit     = body.get("_ep_max_limit")
    default_limit    = int(ep_default_limit) if ep_default_limit is not None else obj.default_limit()
    max_limit        = int(ep_max_limit)     if ep_max_limit     is not None else obj.max_limit()

    if "page" in body:
        page      = max(1, int(body["page"]))
        page_size = int(body.get("page_size", default_limit))
        skip  = (page - 1) * page_size
        limit = page_size
    else:
        skip  = int(body.get("skip",  0))
        limit = int(body.get("limit", default_limit))

    if limit > max_limit:
        raise limit_exceeded(limit, max_limit)

    select_cols   = body.get("select") or body.get("columns")
    select_fields = resolve_select(obj, select_cols, hidden)

    filter_node = body.get("filter")
    params      = ParamBag()
    filter_sql  = (
        compile_filter(filter_node, obj.fields_by_name, params, locale=locale)
        if filter_node else None
    )

    sort_sql = compile_sort(body.get("sort") or [], obj.fields_by_name, locale)

    expand = body.get("expand") or []
    for efield in expand:
        if efield not in obj.fields_by_name:
            raise EngineError("unknown_field", f"Unknown expand field: {efield!r}")
        if not obj.fields_by_name[efield].ref:
            raise EngineError("validation_failed", f"Field '{efield}' has no ref for expand")

    sql = build_list_sql(
        obj,
        select_fields        = select_fields,
        filter_sql           = filter_sql,
        sort_sql             = sort_sql,
        skip                 = skip,
        limit                = limit,
        params               = params,
        expand               = expand,
        locale               = locale,
        fallback             = fallback,
        raw_i18n             = raw_i18n,
        include_window_count = want_window_count,
    )

    logger.info("[engine] LIST SQL:\n%s\n[engine] params: %s", sql, params.values)
    rows = await db.fetch(sql, *params.values)
    if rows:
        logger.info("[engine] First row keys: %s", list(rows[0].keys()))
    else:
        logger.info("[engine] Query returned 0 rows")

    if want_window_count:
        # Strip total_rows from row data and surface it as record_count at the top level.
        raw_rows     = [dict(r) for r in rows]
        record_count = int(raw_rows[0]["total_rows"]) if raw_rows else 0
        result: dict = {
            "ok":           True,
            "record_count": record_count,
            "rows":         [{k: v for k, v in r.items() if k != "total_rows"} for r in raw_rows],
            "skip":         skip,
            "limit":        limit,
        }
    else:
        result = {
            "ok":    True,
            "rows":  [dict(r) for r in rows],
            "skip":  skip,
            "limit": limit,
        }

        if want_count:
            count_cap    = body.get("count_cap")
            count_params = ParamBag()
            if filter_node:
                compile_filter(filter_node, obj.fields_by_name, count_params, locale=locale)
            if count_cap:
                cap                    = int(count_cap)
                raw                    = int(await db.fetchval(build_bounded_count_sql(obj, filter_sql, cap), *count_params.values))
                result["total"]        = raw
                result["total_capped"] = raw > cap
            else:
                result["total"]        = await db.fetchval(build_count_sql(obj, filter_sql), *count_params.values)
                result["total_capped"] = False

    return result


async def _get(obj: ObjectMeta, body: dict, db: asyncpg.Connection, user_email: str | None) -> dict:
    filter_node = body.get("filter")
    if not filter_node:
        raise filter_required()

    locale   = body.get("locale")
    raw_i18n = bool(body.get("raw_i18n", False))
    hidden   = obj.hidden_fields()

    fallback = "en"
    if locale:
        _, fallback = await resolve_locale_chain(locale, db)

    select_fields = resolve_select(obj, body.get("select") or body.get("columns"), hidden)

    params     = ParamBag()
    filter_sql = compile_filter(filter_node, obj.fields_by_name, params, locale=locale)

    # Fetch 2 rows to detect not_unique
    sql = build_list_sql(
        obj,
        select_fields = select_fields,
        filter_sql    = filter_sql,
        sort_sql      = f't."{obj.pk_field}" ASC',
        skip          = 0,
        limit         = 2,
        params        = params,
        expand        = body.get("expand") or [],
        locale        = locale,
        fallback      = fallback,
        raw_i18n      = raw_i18n,
    )
    rows = await db.fetch(sql, *params.values)

    if not rows:
        raise not_found()
    if len(rows) > 1:
        raise not_unique()

    return {"ok": True, "data": dict(rows[0])}


# Parameters × rows threshold — asyncpg hard limit is 32 767
_BULK_VALUES_MAX_PARAMS = 30_000


async def _insert(obj: ObjectMeta, body: dict, db: asyncpg.Connection, user_email: str | None) -> dict:
    if obj.is_view():
        raise unknown_action("insert")

    raw_data = body.get("data")
    if raw_data is None:
        raise EngineError("validation_failed", "'data' is required for insert")

    if isinstance(raw_data, list):
        max_affected = obj.max_affected()
        if len(raw_data) > max_affected:
            raise EngineError(
                "limit_exceeded",
                f"Bulk insert of {len(raw_data)} rows exceeds max_affected={max_affected}",
            )

        # Validate all rows before touching the DB
        cleaned: list[dict] = [validate_insert_data(obj, item) for item in raw_data]

        # Estimate parameter count for strategy selection
        col_count   = max((len(r) for r in cleaned), default=0)
        audit_count = sum(
            1 for col in ("inserted_at", "modified_at", "inserted_by", "modified_by")
            if col in obj.fields_by_name and obj.fields_by_name[col].is_readonly
        )
        estimated_params = (col_count + audit_count) * len(cleaned)

        if estimated_params <= _BULK_VALUES_MAX_PARAMS:
            # Single multi-row INSERT — one round trip to the DB
            params = ParamBag()
            sql    = build_bulk_insert_sql(obj, cleaned, params, user_email)
            try:
                async with db.transaction():
                    rows = await db.fetch(sql, *params.values)
            except Exception as exc:
                raise _pg_error(exc)
            return {"ok": True, "data": [dict(r) for r in rows], "count": len(rows)}

        # Fallback: row-by-row in a single transaction (very large / sparse batches)
        inserted: list[dict] = []
        try:
            async with db.transaction():
                for item in cleaned:
                    row = await _do_insert_cleaned(obj, item, db, user_email)
                    inserted.append(dict(row))
        except Exception as exc:
            raise _pg_error(exc)
        return {"ok": True, "data": inserted, "count": len(inserted)}

    # Single-row insert
    try:
        row = await _do_insert(obj, raw_data, db, user_email)
    except Exception as exc:
        raise _pg_error(exc)
    return {"ok": True, "data": dict(row)}


async def _do_insert(obj: ObjectMeta, raw_data: dict, db: asyncpg.Connection, user_email: str | None):
    cleaned = validate_insert_data(obj, raw_data)
    return await _do_insert_cleaned(obj, cleaned, db, user_email)


async def _do_insert_cleaned(obj: ObjectMeta, cleaned: dict, db: asyncpg.Connection, user_email: str | None):
    params = ParamBag()
    sql    = build_insert_sql(obj, cleaned, params, user_email)
    return await db.fetchrow(sql, *params.values)


async def _update(obj: ObjectMeta, body: dict, db: asyncpg.Connection, user_email: str | None) -> dict:
    if obj.is_view():
        raise unknown_action("update")

    filter_node = body.get("filter")
    if not filter_node:
        raise filter_required()

    raw_data = body.get("data")
    if not raw_data:
        raise EngineError("validation_failed", "'data' is required for update")

    cleaned = validate_update_data(obj, raw_data)

    # Count matching rows first (for expected + max_affected checks)
    count_params = ParamBag()
    filter_sql   = compile_filter(filter_node, obj.fields_by_name, count_params)
    count_sql    = build_count_sql(obj, filter_sql)
    matched      = await db.fetchval(count_sql, *count_params.values)

    expected = body.get("expected")
    if expected is not None and matched != expected:
        raise expected_mismatch(expected, matched)

    max_affected = obj.max_affected()
    if matched > max_affected:
        raise EngineError(
            "limit_exceeded",
            f"Update would affect {matched} rows, exceeding max_affected={max_affected}",
        )

    params     = ParamBag()
    filter_sql = compile_filter(filter_node, obj.fields_by_name, params)
    sql        = build_update_sql(obj, cleaned, filter_sql, params, user_email)

    async with db.transaction():
        rows = await db.fetch(sql, *params.values)

    return {"ok": True, "affected": len(rows), "data": [dict(r) for r in rows]}


async def _delete(obj: ObjectMeta, body: dict, db: asyncpg.Connection, user_email: str | None) -> dict:
    if obj.is_view():
        raise unknown_action("delete")

    filter_node = body.get("filter")
    if not filter_node:
        raise filter_required()

    count_params = ParamBag()
    filter_sql   = compile_filter(filter_node, obj.fields_by_name, count_params)
    count_sql    = build_count_sql(obj, filter_sql)
    matched      = await db.fetchval(count_sql, *count_params.values)

    expected = body.get("expected")
    if expected is not None and matched != expected:
        raise expected_mismatch(expected, matched)

    max_affected = obj.max_affected()
    if matched > max_affected:
        raise EngineError(
            "limit_exceeded",
            f"Delete would affect {matched} rows, exceeding max_affected={max_affected}",
        )

    soft = obj.soft_delete() and "is_active" in obj.fields_by_name

    params     = ParamBag()
    filter_sql = compile_filter(filter_node, obj.fields_by_name, params)
    sql        = build_delete_sql(obj, filter_sql, soft)

    async with db.transaction():
        status = await db.execute(sql, *params.values)

    # asyncpg returns "DELETE N" / "UPDATE N"
    try:
        affected = int(str(status).split()[-1])
    except (ValueError, IndexError):
        affected = matched

    return {"ok": True, "affected": affected}
