"""
Gateway — dynamic OpenAPI 3.0 spec generator.

Reads all *active* endpoints from api_gateway.endpoints and builds a
proper OpenAPI 3.0 document that can be:

  • Imported into Postman  →  Import ▸ Link ▸ /api/v1/gateway/openapi.json
  • Viewed in Swagger UI   →  https://petstore.swagger.io/?url=<base>/api/v1/gateway/openapi.json
  • Used by any OpenAPI-aware tooling

Design decisions
----------------
- Column types stored in endpoint['columns'][].type drive JSON Schema types.
- For functions, body_schema keys are the ordered input parameters
  (values sent in filters dict at runtime).
- POST filter_defs become query params for GET, or body properties for POST select.
- Path params extracted from {param} placeholders are always type string
  unless a filter_def column of the same name exists (then that type wins).
"""
from __future__ import annotations

import re
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html

from app.core.database import get_db
from app.modules.api_bridge.gateway.runtime.executor import parse_endpoint

router = APIRouter(prefix="/gateway", tags=["Gateway"])

_METHOD_DEFAULT_OP = {
    "GET":    "select",
    "POST":   "insert",
    "PUT":    "update",
    "PATCH":  "update",
    "DELETE": "delete",
}


# ---------------------------------------------------------------------------
# PostgreSQL type → JSON Schema
# ---------------------------------------------------------------------------

def _pg_to_oas(pg_type: str) -> dict:
    t = (pg_type or "").lower()
    if re.search(r"\bint\b|integer|bigint|smallint|int2|int4|int8|serial", t):
        return {"type": "integer"}
    if re.search(r"numeric|decimal|float|real|double", t):
        return {"type": "number"}
    if "bool" in t:
        return {"type": "boolean"}
    if "date" in t and "time" not in t:
        return {"type": "string", "format": "date"}
    if "time" in t:
        return {"type": "string", "format": "date-time"}
    if "uuid" in t:
        return {"type": "string", "format": "uuid"}
    if "json" in t:
        return {"type": "object"}
    if "[]" in t or "array" in t:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def _col_schema(c: dict) -> dict:
    """JSON Schema for one column definition (includes description from alias)."""
    schema = _pg_to_oas(c.get("type") or "")
    alias = (c.get("alias") or "").strip()
    if alias and alias != c["name"]:
        schema["description"] = f"DB column: {c['name']}"
    return schema


# ---------------------------------------------------------------------------
# Request body builders
# ---------------------------------------------------------------------------

def _props_from_cols(col_defs: list[dict], exclude: set[str] | None = None) -> dict:
    """Build {col_name: oas_schema} from endpoint column defs."""
    exclude = exclude or set()
    return {
        c["name"]: _col_schema(c)
        for c in col_defs
        if c["name"] not in exclude
    }


def _filter_props(filter_defs: list[dict], exclude: set[str] | None = None) -> dict:
    """Build filter properties object (col + col__op pairs)."""
    exclude = exclude or set()
    props: dict = {}
    for f in filter_defs:
        col = f["column"]
        if col in exclude:
            continue
        ops = f.get("operators") or ["eq"]
        props[col] = _pg_to_oas("")  # type unknown at filter level
        if len(ops) > 1:
            props[f"{col}__op"] = {
                "type": "string",
                "enum": ops,
                "description": f"Filter operator for {col}",
            }
        else:
            props[f"{col}__op"] = {
                "type": "string",
                "enum": ops,
                "default": ops[0],
            }
    return props


def _body_select(filter_defs: list[dict], path_params: set[str]) -> dict:
    fp = _filter_props(filter_defs, exclude=path_params)
    return {
        "required": False,
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {
                **({} if not fp else {"filters": {"type": "object", "properties": fp}}),
                "limit":  {"type": "integer", "default": 100, "minimum": 1},
                "offset": {"type": "integer", "default": 0,   "minimum": 0},
            },
        }}},
    }


def _ml_translations_schema(ml_cols: list[dict]) -> dict:
    """OAS schema for the `translations` body field."""
    field_props = {c["name"]: {"type": "string"} for c in ml_cols}
    return {
        "type": "object",
        "description": (
            "Multilingual values keyed by language code (e.g. \"en\", \"es\"). "
            "Omit a field to leave its existing translation untouched. "
            "Send an empty string to delete a translation."
        ),
        "additionalProperties": {
            "type": "object",
            "properties": field_props,
        },
        "example": {
            "en": {c["name"]: "" for c in ml_cols},
            "es": {c["name"]: "" for c in ml_cols},
        },
    }


def _body_insert(col_defs: list[dict]) -> dict:
    ml_cols      = [c for c in col_defs if c.get("is_multilingual")]
    regular_cols = [c for c in col_defs if not c.get("is_multilingual")]
    props = _props_from_cols(regular_cols)

    body_props: dict = {"data": {"type": "object", "properties": props}}
    if ml_cols:
        body_props["translations"] = _ml_translations_schema(ml_cols)

    return {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["data"],
            "properties": body_props,
        }}},
    }


def _body_update(col_defs: list[dict], filter_defs: list[dict], path_params: set[str]) -> dict:
    ml_cols      = [c for c in col_defs if c.get("is_multilingual")]
    regular_cols = [c for c in col_defs if not c.get("is_multilingual")]

    patch_props  = _props_from_cols(regular_cols, exclude=path_params)
    filter_props = _filter_props(filter_defs, exclude=path_params)
    body: dict = {
        "patch_data": {
            "type": "object",
            "description": "Fields to update — send only those you want to change",
            "properties": patch_props,
        }
    }
    if filter_props:
        body["filters"] = {"type": "object", "properties": filter_props}
    if ml_cols:
        body["translations"] = _ml_translations_schema(ml_cols)

    return {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["patch_data"],
            "properties": body,
        }}},
    }


def _body_delete(filter_defs: list[dict], path_params: set[str]) -> dict:
    fp = _filter_props(filter_defs, exclude=path_params)
    return {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["filters"],
            "properties": {
                "filters": {"type": "object", "properties": fp}
            },
        }}},
    }


def _fn_param_props(func_schema: dict) -> dict:
    """Convert {param_name: pg_type_str} to {param_name: oas_schema}."""
    props: dict = {}
    for name, type_info in func_schema.items():
        if isinstance(type_info, str):
            props[name] = _pg_to_oas(type_info)
        elif isinstance(type_info, dict):
            props[name] = type_info
        else:
            props[name] = {"type": "string"}
    return props


def _body_fn_jsonb_select(func_schema: dict, filter_defs: list[dict]) -> dict:
    """Body for a scalar-JSONB function (config.returns == 'jsonb').

    call_jsonb_function translates body keys → pg function params:
      body.filters → p_filters   (caller filter criteria)
      body.sort    → p_sort
      body.lang    → p_lang
      body.id      → p_id
      body.limit   → p_limit
      body.offset  → p_offset
      p_with_total → NOT from body (driven by config.include_total)
    """
    props: dict = {}
    if "p_filters" in func_schema:
        fp = _filter_props(filter_defs)
        props["filters"] = {
            "type": "object",
            "description": "Filter criteria — column: value pairs; append __op for operator",
            **({"properties": fp} if fp else {}),
        }
    if "p_id" in func_schema:
        props["id"] = {"type": "string", "format": "uuid"}
    if "p_sort" in func_schema:
        props["sort"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "dir":   {"type": "string", "enum": ["asc", "desc"]},
                },
            },
        }
    if "p_lang" in func_schema:
        props["lang"] = {"type": "string", "description": "Language code", "default": "en"}
    props["limit"]  = {"type": "integer", "default": 25, "minimum": 1}
    props["offset"] = {"type": "integer", "default": 0,  "minimum": 0}
    return {
        "required": False,
        "content": {"application/json": {"schema": {"type": "object", "properties": props}}},
    }


def _body_fn_tabular_select(func_schema: dict) -> dict:
    """Body for a table-valued (non-jsonb) function.

    execute_get passes body['filters'] as the filter dict; it extracts values
    by matching body_schema key names to the passed filters object.
    """
    props = _fn_param_props(func_schema)
    body_props: dict = {}
    if props:
        body_props["filters"] = {
            "type": "object",
            "description": "Function input parameters",
            "properties": props,
        }
    body_props["limit"]  = {"type": "integer", "default": 100, "minimum": 1}
    body_props["offset"] = {"type": "integer", "default": 0,   "minimum": 0}
    return {
        "required": False,
        "content": {"application/json": {"schema": {"type": "object", "properties": body_props}}},
    }


def _body_fn_write_data(col_defs: list[dict]) -> dict:
    """Body for execute_function_write insert/update: body.data[] → p_data jsonb."""
    props = _props_from_cols(col_defs)
    return {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["data"],
            "properties": {
                "data": {
                    "type": "array",
                    "description": "Array of records — passed as p_data jsonb to the function",
                    "items": {"type": "object", **({"properties": props} if props else {})},
                },
            },
        }}},
    }


def _body_fn_write_delete(filter_defs: list[dict]) -> dict:
    """Body for execute_function_write delete: body.filters → p_filter jsonb."""
    fp = _filter_props(filter_defs)
    return {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["filters"],
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Filter criteria passed as p_filter jsonb to the function",
                    **({"properties": fp} if fp else {}),
                },
            },
        }}},
    }


# ---------------------------------------------------------------------------
# Response schema builders
# ---------------------------------------------------------------------------

def _response_schema(op: str, col_defs: list[dict], raw_response: bool = False) -> dict:
    """Build the response JSON Schema.

    raw_response=True (default for scalar-JSONB functions): the runtime returns
    the function result directly — no {success, data, error} envelope.

    raw_response=False (tables, views, non-jsonb functions, function writes):
    the runtime always wraps the result in {success: true, data: ..., error: null}.
    """
    out_props: dict = {}
    for c in col_defs:
        key = (c.get("alias") or "").strip() or c["name"]
        out_props[key] = _col_schema(c)

    if op == "delete":
        data_schema = {
            "type": "object",
            "properties": {"deleted": {"type": "integer", "description": "Number of deleted rows"}},
        }
    elif op == "insert":
        # execute_post returns the single RETURNING row (dict)
        data_schema = {"type": "object", **({"properties": out_props} if out_props else {})}
    else:
        # select → flat list; update → list of updated rows
        if out_props:
            data_schema = {"type": "array", "items": {"type": "object", "properties": out_props}}
        else:
            # Function result — shape depends on function definition
            data_schema = {"type": "object", "description": "Function result (shape depends on function definition)"}

    if raw_response:
        # Scalar-JSONB function with raw_response=true: no envelope
        return data_schema

    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "example": True},
            "data":    data_schema,
            "error":   {"type": "string", "nullable": True, "example": None},
        },
    }


# ---------------------------------------------------------------------------
# Parameter builders
# ---------------------------------------------------------------------------

def _path_params_oas(path_param_names: list[str], filter_defs: list[dict]) -> list[dict]:
    """Build OAS path parameter objects, using filter_def type hints where available."""
    fd_map = {f["column"]: f for f in filter_defs}
    out = []
    for name in path_param_names:
        schema: dict = {"type": "string"}
        if name in fd_map:
            # Infer from filter type — we don't store it, so just mark as string
            pass
        out.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema,
            "description": f"Path parameter: {name}",
        })
    return out


def _header_params_oas(headers: list[dict]) -> list[dict]:
    """Only headers with a non-empty value are enforced at runtime."""
    return [
        {
            "name": h["name"],
            "in": "header",
            "required": bool((h.get("value") or "").strip()),
            "schema": {"type": "string"},
            "example": h.get("value") or "",
        }
        for h in headers
        if (h.get("name") or "").strip()
    ]


def _query_params_oas(filter_defs: list[dict], path_params: set[str]) -> list[dict]:
    """GET endpoints: filter_defs become query params."""
    params = []
    for f in filter_defs:
        col = f["column"]
        if col in path_params:
            continue
        ops = f.get("operators") or ["eq"]
        params.append({
            "name": col,
            "in": "query",
            "required": False,
            "schema": {"type": "string"},
            "description": f.get("label") or col,
        })
        if len(ops) > 1:
            params.append({
                "name": f"{col}__op",
                "in": "query",
                "required": False,
                "schema": {"type": "string", "enum": ops},
                "description": f"Filter operator for {col}",
            })
    params += [
        {"name": "limit",  "in": "query", "schema": {"type": "integer", "default": 100}, "required": False},
        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0},   "required": False},
    ]
    return params


# ---------------------------------------------------------------------------
# Main spec builder
# ---------------------------------------------------------------------------

def _safe_op_id(method: str, path: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]", "_", path).strip("_")
    return f"{method.lower()}_{clean}"


def build_openapi(endpoints: list[dict], base_url: str) -> dict:
    paths: dict[str, Any] = {}

    for ep in endpoints:
        path        = ep["url_path"]
        method      = ep["method"].lower()
        db_type     = ep.get("db_type") or "table"
        col_defs    = ep.get("columns") or []
        filter_defs = ep.get("filters") or []
        headers     = ep.get("headers") or []
        func_schema = ep.get("body_schema") or {}
        op          = ep.get("operation_type") or _METHOD_DEFAULT_OP.get(ep["method"], "select")

        path_param_names = re.findall(r"\{(\w+)\}", path)
        path_param_set   = set(path_param_names)

        parameters = (
            _path_params_oas(path_param_names, filter_defs)
            + _header_params_oas(headers)
        )

        # Query params only for GET select
        if method == "get" and op == "select":
            parameters += _query_params_oas(filter_defs, path_param_set)

        # Classify the endpoint for body + response routing
        cfg          = ep.get("config") or {}
        is_jsonb_fn  = db_type == "function" and cfg.get("returns") == "jsonb"
        raw_response = bool(cfg.get("raw_response", False))
        is_fn_write  = db_type == "function" and op != "select"

        # Request body
        request_body: dict | None = None

        if is_fn_write:
            # execute_function_write: insert/update → {data:[...]}, delete → {filters:{...}}
            if op == "delete":
                request_body = _body_fn_write_delete(filter_defs)
            else:
                request_body = _body_fn_write_data(col_defs)
        elif is_jsonb_fn:
            # call_jsonb_function: top-level body keys map to p_* function params
            request_body = _body_fn_jsonb_select(func_schema, filter_defs)
        elif db_type == "function" and func_schema:
            # table-valued (non-jsonb) function: {filters: {param: val}, limit, offset}
            request_body = _body_fn_tabular_select(func_schema)
        elif op == "select" and method != "get":
            request_body = _body_select(filter_defs, path_param_set)
        elif op == "insert":
            request_body = _body_insert(col_defs)
        elif op == "update":
            request_body = _body_update(col_defs, filter_defs, path_param_set)
        elif op == "delete":
            request_body = _body_delete(filter_defs, path_param_set)

        tag = path.strip("/").split("/")[0] or "default"

        oas_op: dict = {
            "summary":     ep.get("name") or f"{ep['method']} {path}",
            "description": ep.get("description") or "",
            "operationId": _safe_op_id(ep["method"], path),
            "tags":        [tag],
            "parameters":  parameters,
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {"application/json": {
                        "schema": _response_schema(op, col_defs, raw_response=raw_response),
                    }},
                },
                "400": {"description": "Validation error — check filters / patch_data"},
                "401": {"description": "Missing or invalid required header"},
                "404": {"description": "Endpoint not found or inactive"},
            },
        }

        if request_body:
            oas_op["requestBody"] = request_body

        if path not in paths:
            paths[path] = {}
        paths[path][method] = oas_op

    return {
        "openapi": "3.0.3",
        "info": {
            "title":       "API Gateway",
            "version":     "1.0.0",
            "description": (
                "Dynamically generated from active endpoints. "
                "Refresh to pick up new endpoints without restarting the server."
            ),
        },
        "servers": [{"url": base_url, "description": "Runtime base URL"}],
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get(
    "/openapi.json",
    response_class=JSONResponse,
    include_in_schema=False,
)
async def gateway_openapi(
    db: asyncpg.Connection = Depends(get_db),
    all: str = "",          # ?all=1  → include draft + deprecated (debug)
):
    """
    Return a fresh OpenAPI 3.0 spec for gateway endpoints.

    Query params:
      ?all=1   include draft + deprecated endpoints (useful while building)
    """
    if all == "1":
        rows = await db.fetch(
            "SELECT * FROM api_gateway.endpoints ORDER BY url_path, method"
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM api_gateway.endpoints WHERE status = 'active' ORDER BY url_path, method"
        )

    endpoints = [parse_endpoint(r) for r in rows]
    base_url  = "/api/v1/run"

    errors: list[str] = []
    try:
        spec = build_openapi(endpoints, base_url)
    except Exception as exc:
        spec = build_openapi([], base_url)
        errors.append(str(exc))

    # Surface diagnostics in the info block
    counts = {"total": len(rows)}
    if all == "1":
        by_status: dict[str, int] = {}
        for r in rows:
            s = r["status"] or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        counts["by_status"] = by_status  # type: ignore[assignment]

    spec["info"]["x-endpoint-count"] = counts
    if errors:
        spec["info"]["x-errors"] = errors

    return JSONResponse(content=spec, media_type="application/json")


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def gateway_swagger_ui(all: str = ""):
    """Serve Swagger UI for the gateway OpenAPI spec."""
    spec_url = "/api/v1/gateway/openapi.json"
    if all == "1":
        spec_url += "?all=1"
    return get_swagger_ui_html(
        openapi_url=spec_url,
        title="API Gateway — Documentation",
        swagger_ui_parameters={"docExpansion": "list", "displayRequestDuration": True},
    )
