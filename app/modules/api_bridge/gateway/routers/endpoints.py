"""
Gateway endpoint admin CRUD.

All routes are POST (admin convention) and require a valid admin JWT.
"""
import json
import logging

import asyncpg
from fastapi import APIRouter, Body, Depends

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.api_bridge.gateway.db.setup import create_schema_translations
from app.modules.api_bridge.engine.registry import registry as _engine_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateway/endpoints", tags=["Gateway"])

VALID_METHODS  = {"GET", "POST", "PUT", "PATCH", "DELETE"}
VALID_DB_TYPES = {"table", "view", "function", "service"}
VALID_STATUSES = {"draft", "active", "deprecated"}
VALID_OP_TYPES = {"select", "insert", "update", "delete"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _auto_register(
    db: asyncpg.Connection,
    schema: str | None,
    obj: str | None,
    url_path: str | None = None,
) -> None:
    """Ensure schema.obj is in toolkit.api_objects so the engine executor has field metadata.

    The gateway URL is resolved via api_gateway.endpoints (not slug lookup),
    so no slug manipulation is needed here.
    """
    if not schema or not obj:
        return
    try:
        await db.execute("SELECT toolkit.fn_register_table($1, $2)", schema, obj)
        await _engine_registry.invalidate(f"{schema}.{obj}")
    except Exception as exc:
        logger.warning("auto_register %s.%s failed: %s", schema, obj, exc)


def _row(r) -> dict:
    """Convert an asyncpg Row to a serialisable dict."""
    d = dict(r)
    for key in ("headers", "body_schema", "filters", "columns", "config"):
        if key in d and not isinstance(d[key], (list, dict)):
            d[key] = json.loads(d[key]) if d[key] else ({} if key in ("body_schema", "config") else [])
    if d.get("inserted_at"):
        d["inserted_at"] = d["inserted_at"].isoformat()
    if d.get("modified_at"):
        d["modified_at"] = d["modified_at"].isoformat()
    # Expose settings stored inside config as top-level fields
    cfg = d.get("config") or {}
    d["pagination_enabled"] = cfg.get("pagination_enabled", True)
    d["default_limit"]      = cfg.get("default_limit", 20)
    d["max_limit"]          = cfg.get("max_limit", 100)
    d["include_total"]      = cfg.get("include_total", False)
    d["count_limit"]        = cfg.get("count_limit", 10_000)
    d["response_extras"]    = cfg.get("response_extras", [])
    d["response_key"]       = cfg.get("response_key", "rows")
    d["resource_code"]      = cfg.get("resource_code") or None
    d["returns"]            = cfg.get("returns") or None
    d["raw_response"]       = bool(cfg.get("raw_response", False))
    d["sort_fields"]        = cfg.get("sort_fields", [])
    d["filter_fields"]      = cfg.get("filter_fields", [])
    d["static_filters"]     = cfg.get("static_filters", {})
    return d


def _validate_common(body: dict) -> tuple:
    """Validate common fields. Returns (name, url_path, method, db_type, op_type)."""
    name     = (body.get("name") or "").strip()
    url_path = (body.get("url_path") or "").strip()
    method   = (body.get("method") or "GET").upper()

    if not name:
        raise ValueError("name is required")
    if not url_path:
        raise ValueError("url_path is required")
    if method not in VALID_METHODS:
        raise ValueError(f"method must be one of {sorted(VALID_METHODS)}")

    db_type = body.get("db_type") or None
    if db_type and db_type not in VALID_DB_TYPES:
        raise ValueError(f"db_type must be one of {sorted(VALID_DB_TYPES)}")

    op_type = body.get("operation_type") or None
    if op_type and op_type not in VALID_OP_TYPES:
        raise ValueError(f"operation_type must be one of {sorted(VALID_OP_TYPES)}")

    return name, url_path, method, db_type, op_type


# ---------------------------------------------------------------------------
# Function introspection helpers
# ---------------------------------------------------------------------------

_SCALAR_RETURN_TYPES = frozenset({"jsonb", "json"})

_INTROSPECT_SQL = """
    with f as (
        select p.oid,
               coalesce(p.proargnames, '{}'::text[])              as names,
               coalesce(p.proallargtypes, p.proargtypes::oid[])   as types,
               p.proargmodes                                       as modes,
               pg_get_function_result(p.oid)                      as result_type
        from   pg_proc p
        join   pg_namespace n on n.oid = p.pronamespace
        where  n.nspname = $1 and p.proname = $2 and p.prokind = 'f'
    )
    select f.result_type,
           coalesce(
               (select jsonb_object_agg(a.name, format_type(a.typ, null))
                from   unnest(f.names, f.types) with ordinality as a(name, typ, ord)
                where  f.modes is null
                   or  f.modes[a.ord::int] in ('i', 'b', 'v')),
               '{}'::jsonb
           ) as body_schema
    from   f
"""


async def _introspect_function(
    db: asyncpg.Connection, schema: str, fn_name: str
) -> tuple[dict, str]:
    """Query pg_proc for body_schema and return type.

    Returns (body_schema_dict, result_type_lower).
    Raises ValueError if the function is missing or overloaded.
    """
    rows = await db.fetch(_INTROSPECT_SQL, schema, fn_name)
    if not rows:
        raise ValueError(f"function {schema}.{fn_name} does not exist")
    if len(rows) > 1:
        raise ValueError(
            f"function {schema}.{fn_name} is overloaded; signature is ambiguous"
        )
    row = rows[0]
    raw = row["body_schema"]
    body_schema = json.loads(raw) if isinstance(raw, str) else (dict(raw) if raw else {})
    return body_schema, (row["result_type"] or "").lower()


async def _resolve_view_columns(
    db: asyncpg.Connection, url_path: str, db_schema: str
) -> list | None:
    """Return column metadata from the entity view, or None if unresolvable.

    Entity name is derived from url_path: /gateway/{entity}/{action}.
    View is looked up via toolkit.api_entities; falls back to {db_schema}.v_{entity}.
    """
    parts = url_path.strip("/").split("/")
    entity = parts[1] if len(parts) >= 3 else None
    if not entity:
        return None

    view_rel = await db.fetchval(
        "SELECT view_rel FROM toolkit.api_entities WHERE entity = $1", entity
    )
    if not view_rel:
        view_rel = f"{db_schema}.v_{entity}"
        logger.warning(
            "entity %r not in toolkit.api_entities; trying fallback view %s",
            entity, view_rel,
        )

    try:
        cols = await db.fetchval(
            """SELECT jsonb_agg(jsonb_build_object(
                          'name',     a.attname,
                          'type',     format_type(a.atttypid, a.atttypmod),
                          'nullable', not a.attnotnull) ORDER BY a.attnum)
               FROM   pg_attribute a
               WHERE  a.attrelid = to_regclass($1)
                 AND  a.attnum > 0 AND NOT a.attisdropped""",
            view_rel,
        )
    except Exception as exc:
        logger.warning("could not read view columns from %s: %s", view_rel, exc)
        return None

    if cols is None:
        return None
    return json.loads(cols) if isinstance(cols, str) else list(cols)


async def _populate_function_fields(
    db:               asyncpg.Connection,
    db_schema:        str,
    db_object:        str,
    url_path:         str,
    config:           dict,
    user_body_schema: dict | None = None,
) -> tuple[dict, dict, list | None]:
    """Run introspection and return (body_schema, config, columns).

    body_schema is always the introspected value — user input is discarded and logged.
    config receives scalar-jsonb defaults merged *under* existing values so manual
    overrides (e.g. a custom max_limit) survive a re-save.
    columns is populated from the entity view for scalar-jsonb functions, else None.
    Raises ValueError on function-not-found or overload ambiguity.
    """
    introspected_schema, result_type = await _introspect_function(db, db_schema, db_object)

    if user_body_schema:
        body_schema = user_body_schema
        if user_body_schema != introspected_schema:
            logger.info(
                "using user-supplied body_schema for %s.%s (differs from introspected)",
                db_schema, db_object,
            )
    else:
        body_schema = introspected_schema

    is_scalar = result_type in _SCALAR_RETURN_TYPES
    if is_scalar:
        defaults: dict = {
            "returns":       "jsonb",
            "raw_response":  True,
            "max_limit":     200,
            "default_limit": 25,
            "include_total": "p_with_total" in body_schema,
        }
        config = {**defaults, **config}

    columns = await _resolve_view_columns(db, url_path, db_schema) if is_scalar else None
    logger.info(
        "introspected %s.%s → result_type=%r keys=%s",
        db_schema, db_object, result_type, list(body_schema.keys()),
    )
    return body_schema, config, columns


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------

@router.post("/list")
async def list_endpoints(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    search    = (body.get("search")   or "").strip()
    status_f  = (body.get("status")   or "").strip()
    page      = max(1, int(body.get("page")      or 1))
    page_size = max(1, min(100, int(body.get("page_size") or 25)))
    offset    = (page - 1) * page_size

    try:
        filters, args = [], []

        if search:
            args.append(f"%{search}%")
            p = len(args)
            filters.append(
                f"(name ILIKE ${p} OR url_path ILIKE ${p}"
                f" OR COALESCE(db_schema,'') ILIKE ${p}"
                f" OR COALESCE(db_object,'') ILIKE ${p})"
            )
        if status_f and status_f in VALID_STATUSES:
            args.append(status_f)
            filters.append(f"status = ${len(args)}")

        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        total = await db.fetchval(
            f"SELECT COUNT(*) FROM api_gateway.endpoints {where}", *args
        )
        rows = await db.fetch(
            f"SELECT * FROM api_gateway.endpoints {where}"
            f" ORDER BY inserted_at DESC"
            f" LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, page_size, offset,
        )
        pages = max(1, (total + page_size - 1) // page_size)
        return ok({
            "rows":      [_row(r) for r in rows],
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     pages,
        })
    except Exception as exc:
        logger.error("list_endpoints: %s", exc)
        return err(str(exc))


@router.post("/get")
async def get_endpoint(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    eid = body.get("id")
    if not eid:
        return err("id required")
    try:
        row = await db.fetchrow(
            "SELECT * FROM api_gateway.endpoints WHERE id=$1", int(eid)
        )
        return ok(_row(row)) if row else err("Not found")
    except Exception as exc:
        logger.error("get_endpoint %s: %s", eid, exc)
        return err(str(exc))


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("/create")
async def create_endpoint(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        name, url_path, method, db_type, op_type = _validate_common(body)
    except ValueError as exc:
        return err(str(exc))

    try:
        db_schema = body.get("db_schema") or None
        db_object = body.get("db_object") or None
        config = body.get("config") or {}
        config["pagination_enabled"] = body.get("pagination_enabled", True)
        # For function endpoints, omit pagination defaults so _populate_function_fields
        # can set the correct scalar-JSONB defaults; only apply body values if explicit.
        if db_type != "function" or body.get("default_limit") is not None:
            config["default_limit"] = body.get("default_limit", 20)
        if db_type != "function" or body.get("max_limit") is not None:
            config["max_limit"]     = body.get("max_limit", 100)
        if db_type != "function" or body.get("include_total") is not None:
            config["include_total"] = body.get("include_total", False)
        config["count_limit"]        = int(body["count_limit"]) if body.get("count_limit") is not None else 10_000
        config["response_extras"]    = body.get("response_extras") or []
        config["response_key"]       = (body.get("response_key") or "rows").strip() or "rows"
        config["resource_code"]      = body.get("resource_code") or None
        if body.get("service_response") is not None:
            config["service_response"] = body["service_response"]
        if body.get("config_returns") is not None:
            config["returns"] = body["config_returns"] or None
        if body.get("config_raw_response") is not None:
            config["raw_response"] = bool(body["config_raw_response"])
        if body.get("sort_fields") is not None:
            config["sort_fields"] = body["sort_fields"] if isinstance(body["sort_fields"], list) else []
        if body.get("filter_fields") is not None:
            config["filter_fields"] = body["filter_fields"] if isinstance(body["filter_fields"], list) else []
        if body.get("static_filters") is not None:
            config["static_filters"] = body["static_filters"] if isinstance(body["static_filters"], dict) else {}

        body_schema_val = body.get("body_schema") or {}
        columns_val     = body.get("columns") or []

        if db_type == "function":
            if not db_schema:
                return err("db_schema is required for function endpoints")
            if not db_object:
                return err("db_object is required for function endpoints")
            try:
                body_schema_val, config, view_cols = await _populate_function_fields(
                    db, db_schema, db_object, url_path, config,
                    user_body_schema=body.get("body_schema"),
                )
                if view_cols is not None and not columns_val:
                    columns_val = view_cols
            except ValueError as exc:
                return err(str(exc))

        row = await db.fetchrow(
            """INSERT INTO api_gateway.endpoints
               (name, url_path, method, db_schema, db_object, db_type,
                headers, body_schema, filters, columns, description, status,
                operation_type, config)
               VALUES ($1,$2,$3,$4,$5,$6,
                       $7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb,
                       $11,$12,$13,$14::jsonb)
               RETURNING *""",
            name, url_path, method,
            db_schema, db_object, db_type,
            json.dumps(body.get("headers") or []),
            json.dumps(body_schema_val),
            json.dumps(body.get("filters") or []),
            json.dumps(columns_val),
            body.get("description") or None,
            body.get("status") or "draft",
            op_type,
            json.dumps(config),
        )
        if db_type in ("table", "view"):
            await _auto_register(db, db_schema, db_object, url_path)
        return ok(_row(row))
    except Exception as exc:
        logger.error("create_endpoint: %s", exc)
        return err(str(exc))


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

@router.post("/update")
async def update_endpoint(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    eid = body.get("id")
    if not eid:
        return err("id required")

    try:
        name, url_path, method, db_type, op_type = _validate_common(body)
    except ValueError as exc:
        return err(str(exc))

    try:
        db_schema = body.get("db_schema") or None
        db_object = body.get("db_object") or None
        config = body.get("config") or {}
        config["pagination_enabled"] = body.get("pagination_enabled", True)
        # For function endpoints, omit pagination defaults so _populate_function_fields
        # can set the correct scalar-JSONB defaults; only apply body values if explicit.
        if db_type != "function" or body.get("default_limit") is not None:
            config["default_limit"] = body.get("default_limit", 20)
        if db_type != "function" or body.get("max_limit") is not None:
            config["max_limit"]     = body.get("max_limit", 100)
        if db_type != "function" or body.get("include_total") is not None:
            config["include_total"] = body.get("include_total", False)
        config["count_limit"]        = int(body["count_limit"]) if body.get("count_limit") is not None else 10_000
        config["response_extras"]    = body.get("response_extras") or []
        config["response_key"]       = (body.get("response_key") or "rows").strip() or "rows"
        config["resource_code"]      = body.get("resource_code") or None
        if body.get("service_response") is not None:
            config["service_response"] = body["service_response"]
        if body.get("config_returns") is not None:
            config["returns"] = body["config_returns"] or None
        if body.get("config_raw_response") is not None:
            config["raw_response"] = bool(body["config_raw_response"])
        if body.get("sort_fields") is not None:
            config["sort_fields"] = body["sort_fields"] if isinstance(body["sort_fields"], list) else []
        if body.get("filter_fields") is not None:
            config["filter_fields"] = body["filter_fields"] if isinstance(body["filter_fields"], list) else []
        if body.get("static_filters") is not None:
            config["static_filters"] = body["static_filters"] if isinstance(body["static_filters"], dict) else {}

        body_schema_val = body.get("body_schema") or {}
        columns_val     = body.get("columns") or []

        if db_type == "function":
            if not db_schema:
                return err("db_schema is required for function endpoints")
            if not db_object:
                return err("db_object is required for function endpoints")
            try:
                body_schema_val, config, view_cols = await _populate_function_fields(
                    db, db_schema, db_object, url_path, config,
                    user_body_schema=body.get("body_schema"),
                )
                if view_cols is not None and not columns_val:
                    columns_val = view_cols
            except ValueError as exc:
                return err(str(exc))

        # Pre-flight: check for (method, url_path) collision with a different endpoint
        conflict = await db.fetchval(
            "SELECT id FROM api_gateway.endpoints WHERE method=$1 AND url_path=$2 AND id != $3",
            method, url_path, int(eid),
        )
        if conflict:
            return err(f"Another endpoint (id={conflict}) already uses {method} {url_path}")

        row = await db.fetchrow(
            """UPDATE api_gateway.endpoints SET
               name=$2, url_path=$3, method=$4,
               db_schema=$5, db_object=$6, db_type=$7,
               headers=$8::jsonb, body_schema=$9::jsonb,
               filters=$10::jsonb, columns=$11::jsonb,
               description=$12, status=$13, operation_type=$14,
               config=$15::jsonb, modified_at=NOW()
               WHERE id=$1 RETURNING *""",
            int(eid),
            name, url_path, method,
            db_schema, db_object, db_type,
            json.dumps(body.get("headers") or []),
            json.dumps(body_schema_val),
            json.dumps(body.get("filters") or []),
            json.dumps(columns_val),
            body.get("description") or None,
            body.get("status") or "draft",
            op_type,
            json.dumps(config),
        )
        if not row:
            return err("Not found")
        if db_type in ("table", "view"):
            await _auto_register(db, db_schema, db_object, url_path)
        return ok(_row(row))
    except Exception as exc:
        logger.error("update_endpoint %s: %s", eid, exc)
        return err(str(exc))


# ---------------------------------------------------------------------------
# Delete / Status
# ---------------------------------------------------------------------------

@router.post("/delete")
async def delete_endpoint(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    eid = body.get("id")
    if not eid:
        return err("id required")
    try:
        row = await db.fetchrow(
            "DELETE FROM api_gateway.endpoints WHERE id=$1 RETURNING id", int(eid)
        )
        if row is None:
            return err("Not found")
        return ok({"deleted": True, "id": int(eid)})
    except Exception as exc:
        logger.error("delete_endpoint %s: %s", eid, exc)
        return err(str(exc))


@router.post("/set-status")
async def set_status(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    eid    = body.get("id")
    status = body.get("status")
    if not eid:
        return err("id required")
    if status not in VALID_STATUSES:
        return err(f"status must be one of {sorted(VALID_STATUSES)}")
    try:
        row = await db.fetchrow(
            "UPDATE api_gateway.endpoints SET status=$2, modified_at=NOW() "
            "WHERE id=$1 RETURNING *",
            int(eid), status,
        )
        return ok(_row(row)) if row else err("Not found")
    except Exception as exc:
        logger.error("set_status %s→%s: %s", eid, status, exc)
        return err(str(exc))


# ---------------------------------------------------------------------------
# Schema setup — create translations table in a business schema
# ---------------------------------------------------------------------------

@router.post("/schema/setup-translations")
async def setup_schema_translations(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """
    Create the standard `translations` table in the given schema (idempotent).

    Body: { "schema": "company" }

    Run this once per business schema before using multilingual endpoint fields
    that write to that schema.
    """
    schema = (body.get("schema") or "").strip()
    if not schema:
        return err("schema is required")
    try:
        await create_schema_translations(db, schema)
        return ok({"schema": schema, "table": "translations", "created": True})
    except Exception as exc:
        logger.error("setup_schema_translations %s: %s", schema, exc)
        return err(str(exc))
