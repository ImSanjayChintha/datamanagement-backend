"""Catch-all routes for the metadata-driven CRUD engine.

Registered on the FastAPI app with the /api/v1 prefix so they resolve to:
  GET|POST  /api/v1/{schema}/{object_name}/{action}
  POST      /api/v1/{schema}/fn/{function_name}
  POST      /api/v1/meta
  POST      /api/v1/meta/reload

GET requests use an empty body (returns all rows with default pagination).
POST requests accept a JSON body for filtering, sorting, and pagination.
"""
from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.database import get_db
from app.core.deps import get_engine_caller
from app.modules.api_bridge.engine.errors import EngineError
from app.modules.api_bridge.engine.executor import execute_action
from app.modules.api_bridge.engine.meta import build_meta
from app.modules.api_bridge.engine.registry import registry
from app.modules.api_bridge.engine.resource_auth import validate_resource_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["API Engine"])


def _parse_auth(admin: dict) -> tuple[str | None, list[str]]:
    email = admin.get("email")
    role  = admin.get("role", "reader")
    return email, [role]


async def _body(request: Request) -> dict:
    """Return parsed JSON body for POST; empty dict for GET."""
    if request.method == "GET":
        return {}
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.post(
    "/meta",
    summary="Describe registered objects",
    description=(
        "Returns metadata for all registered API objects visible to the caller's role. "
        "Pass `{\"object\": \"schema.table\"}` to describe a single object."
    ),
)
async def api_meta(
    request: Request,
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict               = Depends(get_engine_caller),
):
    email, roles = _parse_auth(admin)
    body = await _body(request)
    try:
        result = await build_meta(body, db, roles)
        return {"ok": True, **result}
    except EngineError as e:
        return e.to_response()
    except Exception:
        logger.exception("api_meta error")
        return JSONResponse({"ok": False, "error": "internal"}, status_code=500)


@router.post(
    "/meta/reload",
    summary="Flush registry cache",
    description="Clears the in-process object/field registry cache. New requests will reload from the database.",
)
async def api_meta_reload(
    admin: dict = Depends(get_engine_caller),
):
    await registry.invalidate()
    return {"ok": True, "message": "Registry cache cleared"}


_GW_PROTECTED_KEYS = frozenset({"ok", "rows", "record_count", "total", "skip", "limit", "total_capped", "error", "message"})

_GW_DIRECT_KEYS = frozenset({
    "limit", "skip", "offset", "page", "page_size",
    "locale", "sort", "select", "count", "raw_i18n",
})
_GW_INT_KEYS = frozenset({"limit", "skip", "offset", "page", "page_size"})


@router.api_route(
    "/gateway/{object_slug}/{action}",
    methods=["GET", "POST", "PATCH", "DELETE"],
    summary="Execute a saved gateway endpoint",
    description=(
        "Looks up the request URL in `api_gateway.endpoints`, reads the stored "
        "`db_schema` / `db_object`, then executes the action against that object.\n\n"
        "The URL slug is an arbitrary name — it resolves to whatever schema/object "
        "was configured when the endpoint was saved."
    ),
)


async def api_gateway_endpoint(
    object_slug: str,
    action:      str,
    request: Request,
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict               = Depends(get_engine_caller),
):
    email, roles = _parse_auth(admin)
    body     = await _body(request)
    url_path = f"/gateway/{object_slug}/{action}"

    # For GET requests build the engine body DSL from query-string params.
    # ?col=value             → {"field": "col", "op": "eq", "value": "value"}
    # ?limit=50&offset=0     → passed through directly as pagination keys
    # Values wrapped in SQL-style single quotes (e.g. 'speed') are stripped.
    if request.method == "GET":
        leaves: list[dict] = []
        direct: dict = {}
        for k, v in request.query_params.items():
            if k in _GW_DIRECT_KEYS:
                try:
                    direct[k] = int(v) if k in _GW_INT_KEYS else v
                except ValueError:
                    direct[k] = v
            else:
                clean = v.strip("'")   # strip accidental SQL-style string quotes
                leaves.append({"field": k, "op": "eq", "value": clean})
        body = {**direct}
        if len(leaves) == 1:
            body["filter"] = leaves[0]
        elif leaves:
            body["filter"] = {"and": leaves}

    endpoint = await db.fetchrow(
        """
        SELECT db_schema, db_object, config, columns
        FROM   api_gateway.endpoints
        WHERE  url_path = $1
          AND  status = 'active'
        LIMIT  1
        """,
        url_path,
    )
    if not endpoint:
        return JSONResponse(
            {"ok": False, "error": "not_found",
             "message": f"No active endpoint for: {url_path}"},
            status_code=404,
        )

    schema      = endpoint["db_schema"]
    object_name = endpoint["db_object"]
    cfg         = endpoint["config"] or {}
    ep_cols     = endpoint["columns"] or []

    if not schema or not object_name:
        return JSONResponse(
            {"ok": False, "error": "misconfigured",
             "message": "Endpoint has no schema/object configured"},
            status_code=500,
        )

    # List always returns total via COUNT(*) OVER() — no toggle needed.
    if action == "list":
        body = {**body, "count_window": True}
    else:
        body.pop("count", None)
        body.pop("count_window", None)

    # Column selection — if the endpoint has a column list, restrict the SELECT.
    # Accepts both new format ["col1","col2"] and old [{name:"col1"},…].
    if ep_cols:
        col_names: list[str] = []
        for c in ep_cols:
            if isinstance(c, str) and c:
                col_names.append(c)
            elif isinstance(c, dict) and c.get("name"):
                col_names.append(c["name"])
        if col_names:
            body = {**body, "select": col_names}

    # Inject endpoint pagination config so the executor uses it instead of
    # the object-level policy defaults from toolkit.api_objects.
    if cfg.get("pagination_enabled", True):
        body = {
            **body,
            "_ep_default_limit": int(cfg.get("default_limit") or 20),
            "_ep_max_limit":     int(cfg.get("max_limit")     or 100),
        }

    # Response array key (what the rows list is named in the output).
    _RESERVED = frozenset({"ok", "error", "message", "skip", "limit", "record_count", "total", "total_capped"})
    response_key = (cfg.get("response_key") or "rows").strip() or "rows"
    if response_key in _RESERVED:
        response_key = "rows"

    try:
        obj = await registry.get(f"{schema}.{object_name}", db)
        if obj is not None:
            await validate_resource_auth(obj, request, db)
        result = await execute_action(schema, object_name, action, body, db, email, roles)

        # Merge extra fields defined on the endpoint
        if isinstance(result, dict):
            extras = cfg.get("response_extras") or []
            if extras:
                top_keys  = [k for k in result if k not in ("rows",)]
                row_keys  = list(result["rows"][0].keys()) if result.get("rows") else []
                row_count = len(result.get("rows") or [])
                logger.info(
                    "[gateway extras] url=%s  top-level keys=%s  row_count=%d  first_row_keys=%s",
                    url_path, top_keys, row_count, row_keys,
                )

            for extra in extras:
                key = (extra.get("key") or "").strip()
                if not key:
                    continue
                if extra.get("type") == "field":
                    source = (extra.get("value") or "").strip()
                    if source.startswith("rows."):
                        field_name = source[5:]
                        rows_data  = result.get("rows") or []
                        if not rows_data:
                            resolved = 0
                            logger.info("[gateway extras] key=%r source=rows.%r — rows empty, returning 0", key, field_name)
                        elif field_name not in rows_data[0]:
                            resolved = 0
                            logger.warning(
                                "[gateway extras] key=%r source=rows.%r — field not found in first row. "
                                "Available keys: %s. Returning 0.",
                                key, field_name, list(rows_data[0].keys()),
                            )
                        else:
                            resolved = rows_data[0][field_name]
                            if resolved is None:
                                resolved = 0
                                logger.info("[gateway extras] key=%r source=rows.%r — field is NULL, returning 0", key, field_name)
                            else:
                                logger.info("[gateway extras] key=%r source=rows.%r — resolved=%r", key, field_name, resolved)
                    else:
                        resolved = result.get(source)
                        if resolved is None:
                            resolved = 0
                            logger.warning("[gateway extras] key=%r source=%r — not found in result, returning 0", key, source)
                        else:
                            logger.info("[gateway extras] key=%r source=%r — resolved=%r", key, source, resolved)

                    if key in _GW_PROTECTED_KEYS:
                        logger.warning("[gateway extras] key=%r is protected — skipping", key)
                    else:
                        result[key] = resolved
                else:
                    if key not in _GW_PROTECTED_KEYS:
                        result[key] = extra.get("value")
                    else:
                        logger.warning("[gateway extras] key=%r is protected — skipping static override", key)

            # Rename the rows array to the configured response key (done last
            # so extras can't accidentally overwrite it).
            if response_key != "rows" and "rows" in result:
                result[response_key] = result.pop("rows")

        return result
    except EngineError as e:
        return e.to_response()
    except Exception:
        logger.exception(
            "api_gateway_endpoint error: %s → %s.%s/%s",
            url_path, schema, object_name, action,
        )
        return JSONResponse({"ok": False, "error": "internal"}, status_code=500)


@router.get("/engine/openapi.json", include_in_schema=False)
async def engine_openapi(
    db: asyncpg.Connection = Depends(get_db),
):
    """Generate OpenAPI 3.0 spec from toolkit.api_objects + toolkit.api_fields."""
    import json as _json

    obj_rows = await db.fetch(
        "SELECT code, schema_name, object_name, name, kind, actions "
        "FROM toolkit.api_objects WHERE is_active = TRUE ORDER BY schema_name, object_name"
    )
    field_rows = await db.fetch(
        "SELECT object_code, field_name, field_type, name, is_required, is_readonly "
        "FROM toolkit.api_fields WHERE is_active = TRUE ORDER BY object_code, sort_order"
    )

    fields_by_obj: dict = {}
    for fr in field_rows:
        fields_by_obj.setdefault(fr["object_code"], []).append(dict(fr))

    def _ft_oas(ft: str) -> dict:
        t = (ft or "text").lower()
        if t == "integer":                  return {"type": "integer"}
        if t in ("number", "decimal"):      return {"type": "number"}
        if t == "boolean":                  return {"type": "boolean"}
        if t == "date":                     return {"type": "string", "format": "date"}
        if t == "datetime":                 return {"type": "string", "format": "date-time"}
        if t == "multiselect":              return {"type": "array", "items": {"type": "string"}}
        if t in ("json", "i18n_text", "i18n_richtext"): return {"type": "object"}
        return {"type": "string"}

    paths:   dict = {}
    schemas: dict = {}

    for obj in obj_rows:
        code   = obj["code"]
        sch    = obj["schema_name"]
        tbl    = obj["object_name"]
        label  = obj["name"] or tbl
        fields = fields_by_obj.get(code, [])

        raw = obj["actions"]
        try:
            actions: dict = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            actions = {}

        # Build component schema from registered fields
        sk  = code.replace(".", "_")
        ref = f"#/components/schemas/{sk}"
        props: dict = {}
        reqs: list  = []
        for f in fields:
            oas = _ft_oas(f.get("field_type") or "text")
            n   = f.get("name") or f["field_name"]
            if n != f["field_name"]:
                oas = {**oas, "description": n}
            props[f["field_name"]] = oas
            if f.get("is_required") and not f.get("is_readonly"):
                reqs.append(f["field_name"])
        schemas[sk] = {"type": "object", "properties": props, **({"required": reqs} if reqs else {})}

        _COMMON = {"tags": [sch], "security": [{"bearerAuth": []}]}
        _ERRS   = {
            "400": {"description": "Validation error"},
            "401": {"description": "Unauthorized"},
            "404": {"description": "Object not found"},
        }

        for action in actions:
            path_key = f"/{sch}/{tbl}/{action}"
            paths.setdefault(path_key, {})

            if action == "list":
                op = {
                    **_COMMON,
                    "summary": f"List {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "filter":       {"type": "object",  "description": "Filter expression"},
                            "sort":         {"type": "array",   "description": "Sort rules [{field, dir}]"},
                            "limit":        {"type": "integer", "default": 25},
                            "skip":         {"type": "integer", "default": 0},
                            "select":       {"type": "array",   "items": {"type": "string"}},
                            "count":        {"type": "boolean", "default": False},
                            "count_window": {"type": "boolean", "default": False},
                            "locale":       {"type": "string"},
                        },
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "ok":           {"type": "boolean"},
                                "rows":         {"type": "array", "items": {"$ref": ref}},
                                "skip":         {"type": "integer"},
                                "limit":        {"type": "integer"},
                                "record_count": {"type": "integer"},
                            },
                        }}}},
                    },
                }

            elif action == "get":
                op = {
                    **_COMMON,
                    "summary": f"Get {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["filter"],
                        "properties": {
                            "filter": {"type": "object", "description": "Unique record filter"},
                            "locale": {"type": "string"},
                        },
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}, "data": {"$ref": ref}},
                        }}}},
                    },
                }

            elif action == "insert":
                op = {
                    **_COMMON,
                    "summary": f"Insert {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["data"],
                        "properties": {
                            "data": {"oneOf": [{"$ref": ref}, {"type": "array", "items": {"$ref": ref}}]},
                        },
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "ok":    {"type": "boolean"},
                                "data":  {"$ref": ref},
                                "count": {"type": "integer"},
                            },
                        }}}},
                    },
                }

            elif action == "update":
                op = {
                    **_COMMON,
                    "summary": f"Update {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["filter", "data"],
                        "properties": {
                            "filter":   {"type": "object"},
                            "data":     {"$ref": ref},
                            "expected": {"type": "integer", "description": "Guard: expected row count"},
                        },
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "ok":       {"type": "boolean"},
                                "affected": {"type": "integer"},
                                "data":     {"type": "array", "items": {"$ref": ref}},
                            },
                        }}}},
                    },
                }

            elif action == "delete":
                op = {
                    **_COMMON,
                    "summary": f"Delete {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["filter"],
                        "properties": {
                            "filter":   {"type": "object"},
                            "expected": {"type": "integer"},
                        },
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "ok":       {"type": "boolean"},
                                "affected": {"type": "integer"},
                            },
                        }}}},
                    },
                }

            elif action in ("upsert", "sync"):
                op = {
                    **_COMMON,
                    "summary": f"{action.capitalize()} {label}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["data"],
                        "properties": {"data": {"type": "array", "items": {"$ref": ref}}},
                    }}}},
                    "responses": {
                        **_ERRS,
                        "200": {"description": "Success", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                        }}}},
                    },
                }

            else:
                op = {
                    **_COMMON,
                    "summary": f"{label}: {action}",
                    "operationId": f"{code}_{action}",
                    "requestBody": {
                        "required": False,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {**_ERRS, "200": {"description": "Success"}},
                }

            paths[path_key]["post"] = op

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title":       "API Engine",
            "version":     "1.0.0",
            "description": (
                "Dynamic CRUD engine — objects registered in toolkit.api_objects. "
                "All endpoints require Bearer token authentication."
            ),
        },
        "servers": [{"url": "/api/v1", "description": "Engine base URL"}],
        "components": {
            "schemas": schemas,
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        },
        "security": [{"bearerAuth": []}],
        "paths": paths,
    }
    return JSONResponse(content=spec, media_type="application/json")


@router.get("/engine/docs", include_in_schema=False)
async def engine_docs():
    """Swagger UI for the engine OpenAPI spec."""
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse  # noqa: F811 (re-import is fine here)
    return get_swagger_ui_html(
        openapi_url="/api/v1/engine/openapi.json",
        title="API Engine — Documentation",
        swagger_ui_parameters={"docExpansion": "list", "displayRequestDuration": True},
    )


@router.api_route(
    "/{schema}/{object_name}/{action}",
    methods=["GET", "POST"],
    summary="Execute an object action",
    description=(
        "Dispatches to the metadata-driven CRUD engine.\n\n"
        "**GET** — list with default pagination (no filters).\n\n"
        "**POST** — full body DSL: `filter`, `sort`, `limit`, `offset`, `locale`, `data`, `pk`.\n\n"
        "Common actions: `list`, `get`, `insert`, `update`, `delete`."
    ),
)
async def api_data(
    schema:      str,
    object_name: str,
    action:      str,
    request: Request,
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict               = Depends(get_engine_caller),
):
    email, roles = _parse_auth(admin)
    body = await _body(request)
    try:
        obj_code = f"{schema}.{object_name}"
        obj = await registry.get(obj_code, db)
        if obj is not None:
            await validate_resource_auth(obj, request, db)
        result = await execute_action(schema, object_name, action, body, db, email, roles)
        return result
    except EngineError as e:
        return e.to_response()
    except Exception:
        logger.exception("api_data error: %s/%s/%s", schema, object_name, action)
        return JSONResponse({"ok": False, "error": "internal"}, status_code=500)


@router.post(
    "/{schema}/fn/{function_name}",
    summary="Call a registered function",
    description="Invokes a PostgreSQL function registered in `toolkit.api_objects` with `kind='function'`.",
)
async def api_function(
    schema:        str,
    function_name: str,
    request: Request,
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict               = Depends(get_engine_caller),
):
    from app.modules.api_bridge.engine.errors import (
        unknown_object, unknown_action, forbidden as eng_forbidden,
    )
    from app.modules.api_bridge.engine.functions import execute_function

    email, roles = _parse_auth(admin)
    obj_code = f"{schema}.{function_name}"
    body = await _body(request)

    try:
        obj = await registry.get(obj_code, db)
        if obj is None or obj.kind != "function":
            raise unknown_object(obj_code)

        await validate_resource_auth(obj, request, db)

        allowed = obj.allowed_roles("call") or obj.allowed_roles("list") or ["*"]
        if "*" not in allowed and not set(roles) & set(allowed):
            raise eng_forbidden()

        fn_name = f"{schema}.{function_name}"
        return await execute_function(fn_name, body, db, email)

    except EngineError as e:
        return e.to_response()
    except Exception:
        logger.exception("api_function error: %s/fn/%s", schema, function_name)
        return JSONResponse({"ok": False, "error": "internal"}, status_code=500)
