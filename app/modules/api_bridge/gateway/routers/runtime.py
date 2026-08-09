"""
Gateway runtime router — serves defined API endpoints at their configured paths.

Every handler:
  1. Loads the endpoint definition (must be status='active').
  2. Validates any required headers declared on the endpoint.
  3. Delegates SQL execution to runtime.executor.
  4. Returns the standard {success, data, error} envelope.

URL prefix : /run
Full path  : /api/v1/run/<url_path defined on the endpoint>

HTTP error mapping
------------------
404  endpoint not found or inactive
401  missing / wrong required header
400  bad request — invalid filter, missing data, no filter for UPDATE/DELETE
409  unique constraint violation
500  unexpected database error (detail logged server-side, not exposed to client)
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import asyncpg
import asyncpg.exceptions as pg_exc
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.core.database import get_db
from app.core.response import ok
from app.modules.api_bridge.gateway.runtime.executor import (
    call_jsonb_function,
    load_endpoint,
    effective_op,
    execute_delete,
    execute_function_write,
    execute_get,
    execute_patch,
    execute_post,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/run", tags=["Gateway Runtime"])


# ---------------------------------------------------------------------------
# Service dispatch
# ---------------------------------------------------------------------------

async def _dispatch_service(
    endpoint:   dict,
    body:       dict,
    db:         asyncpg.Connection,
    full_path:  str,
    user_email: str | None = None,
) -> Any:
    """Route the request to a registered Python service class."""
    from app.modules.api_bridge.services.registry import get_service  # lazy import

    svc_name = (endpoint.get("db_object") or "").strip()
    svc = get_service(svc_name)
    if not svc:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{svc_name}' is not registered. Check the service registry.",
        )
    try:
        result = await svc.execute(
            body=body,
            path_params=endpoint.get("_path_params") or {},
            db=db,
            user_email=user_email,
        )
        return ok(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Service '%s' error on %s: %s", svc_name, full_path, exc)
        raise HTTPException(status_code=500, detail="Service execution error.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_active(db: asyncpg.Connection, url_path: str, method: str) -> dict:
    """Load an active endpoint or raise 404."""
    endpoint = await load_endpoint(db, f"/{url_path}", method)
    if not endpoint:
        raise HTTPException(
            status_code=404,
            detail=f"No active {method} endpoint at /{url_path}",
        )
    return endpoint


def _validate_headers(endpoint: dict, request: Request) -> None:
    """
    Enforce required headers declared on the endpoint.
    A header def with a non-empty `value` field is enforced at runtime.
    Defs with an empty value are documentation-only — not enforced.
    """
    for hdr in endpoint.get("headers") or []:
        name     = (hdr.get("name") or "").strip()
        expected = (hdr.get("value") or "").strip()
        if not name or not expected:
            continue
        if request.headers.get(name) != expected:
            raise HTTPException(
                status_code=401,
                detail=f"Missing or invalid header: {name}",
            )


async def _validate_resource_auth(
    endpoint: dict,
    request:  Request,
    db:       asyncpg.Connection,
) -> None:
    """Enforce the auth policy of the resource linked to this endpoint.

    No-ops when the endpoint has no resource_code or auth_type is 'none'.
    If the resource record is missing / inactive the request is allowed through
    so that a misconfigured resource doesn't break unrelated endpoints.
    """
    resource_code = (endpoint.get("resource_code") or "").strip()
    if not resource_code:
        return

    resource = await db.fetchrow(
        "SELECT auth_type, auth_config FROM api_gateway.api_resources WHERE code=$1 AND is_active=TRUE",
        resource_code,
    )
    if not resource:
        return

    auth_type = (resource["auth_type"] or "none").lower()
    cfg: dict = dict(resource["auth_config"] or {})

    if auth_type == "none":
        return

    if auth_type == "bearer":
        prefix   = (cfg.get("prefix") or "Bearer").strip()
        token    = (cfg.get("token")  or "").strip()
        if not token:
            return
        if request.headers.get("authorization", "") != f"{prefix} {token}":
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

    elif auth_type == "basic":
        username = (cfg.get("username") or "").strip()
        password = (cfg.get("password") or "").strip()
        if not username:
            return
        auth_hdr = request.headers.get("authorization", "")
        if not auth_hdr.startswith("Basic "):
            raise HTTPException(status_code=401, detail="Basic authentication required")
        try:
            decoded           = base64.b64decode(auth_hdr[6:]).decode()
            req_user, req_pwd = decoded.split(":", 1)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid basic auth encoding")
        if req_user != username or req_pwd != password:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    elif auth_type == "api_key":
        key_name  = (cfg.get("key_name")  or "").strip()
        key_value = (cfg.get("key_value") or "").strip()
        location  = cfg.get("location", "header")
        if not key_name or not key_value:
            return
        if location == "query":
            if request.query_params.get(key_name, "") != key_value:
                raise HTTPException(status_code=401, detail=f"Invalid or missing {key_name} query parameter")
        else:
            if request.headers.get(key_name.lower(), "") != key_value:
                raise HTTPException(status_code=401, detail=f"Invalid or missing {key_name} header")


# SQLSTATE → (http_status, error_kind) for codes the engine raises deliberately.
# Checked before the isinstance guards so specific codes win.
# exc.message is safe to return (names the offending field/type).
# exc.context is never returned — it contains the generated SQL.
_SQLSTATE_HTTP: dict[str, tuple[int, str]] = {
    "42703": (422, "unknown field"),
    "42501": (403, "field not filterable"),
    "22023": (422, "invalid filter"),
    "22P02": (422, "invalid value for field type"),
    "42P01": (404, "unknown entity"),
    "P0002": (404, "not found"),
    "42883": (500, "function does not exist"),
}


def _raise_db_error(exc: Exception, path: str, endpoint: dict | None = None) -> None:
    if isinstance(exc, asyncpg.PostgresError):
        mapped = _SQLSTATE_HTTP.get(getattr(exc, "sqlstate", None))
        if mapped:
            status, kind = mapped
            logger.warning(
                "DB %s on %s:\n"
                "  message  : %s\n"
                "  detail   : %s\n"
                "  hint     : %s\n"
                "  context  : %s\n"
                "  position : %s\n"
                "  endpoint : db_type=%s db_object=%s config=%s body_schema=%s",
                getattr(exc, "sqlstate", "?"), path,
                getattr(exc, "message", ""),
                getattr(exc, "detail",   None),
                getattr(exc, "hint",     None),
                getattr(exc, "context",  None),
                getattr(exc, "position", None),
                endpoint.get("db_type")    if endpoint else "?",
                endpoint.get("db_object")  if endpoint else "?",
                endpoint.get("config")     if endpoint else "?",
                endpoint.get("body_schema") if endpoint else "?",
            )
            raise HTTPException(
                status_code=status,
                detail={"error": kind, "message": getattr(exc, "message", "")},
            )
    if isinstance(exc, pg_exc.UniqueViolationError):
        raise HTTPException(status_code=409, detail="Duplicate value — record already exists.")
    if isinstance(exc, (pg_exc.ForeignKeyViolationError, pg_exc.NotNullViolationError,
                        pg_exc.CheckViolationError)):
        raise HTTPException(status_code=400, detail=f"Database constraint violation: {exc.detail or exc.message}")
    if isinstance(exc, asyncpg.PostgresError):
        logger.error(
            "DB error on %s: [%s] %s | detail=%s | hint=%s | context=%s",
            path,
            getattr(exc, "sqlstate", "?"),
            getattr(exc, "message", ""),
            getattr(exc, "detail",  None),
            getattr(exc, "hint",    None),
            getattr(exc, "context", None),
        )
        raise HTTPException(status_code=500, detail="Internal server error")
    raise exc


def _user_email(request: Request) -> str | None:
    return getattr(request.state, "user_email", None)


def _is_jsonb_fn(endpoint: dict) -> bool:
    """True when this endpoint should use call_jsonb_function.

    Primary:  config.returns == "jsonb"  (explicit — set by seed scripts / admin UI).
    Fallback: returns is null AND body_schema declares the JSONB-function param set
              (p_filters + p_sort).  Covers endpoints created before the admin UI
              exposed the returns field.  Endpoints where returns is explicitly set
              to something other than "jsonb" are never treated as JSONB functions.
    """
    if endpoint.get("db_type") != "function":
        return False
    cfg     = endpoint.get("config") or {}
    returns = cfg.get("returns")
    if returns == "jsonb":
        return True
    if returns is not None:
        return False
    # returns is null — auto-detect from body_schema
    sig = set(endpoint.get("body_schema") or {})
    return "p_filters" in sig and "p_sort" in sig


# ---------------------------------------------------------------------------
# GET — SELECT with query-string filters
# ---------------------------------------------------------------------------

@router.get("/{full_path:path}")
async def run_get(
    full_path: str,
    request:   Request,
    db: asyncpg.Connection = Depends(get_db),
):
    endpoint = await _get_active(db, full_path, "GET")
    _validate_headers(endpoint, request)
    await _validate_resource_auth(endpoint, request, db)

    if endpoint.get("db_type") == "service":
        return await _dispatch_service(endpoint, dict(request.query_params), db, full_path, _user_email(request))

    if _is_jsonb_fn(endpoint):
        raise HTTPException(status_code=405,
                            detail="This endpoint requires POST with a JSON body.")

    qs = dict(request.query_params)
    try:
        limit  = int(qs.pop("limit",  100))
        offset = int(qs.pop("offset", 0))
        rows = await execute_get(db, endpoint, filters=qs, limit=limit, offset=offset)
        return ok(rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _raise_db_error(exc, full_path)


# ---------------------------------------------------------------------------
# POST — INSERT by default; SELECT / UPDATE / DELETE via operation_type
# ---------------------------------------------------------------------------

@router.post("/{full_path:path}")
async def run_post(
    full_path: str,
    request:   Request,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    endpoint = await _get_active(db, full_path, "POST")
    _validate_headers(endpoint, request)
    await _validate_resource_auth(endpoint, request, db)

    if endpoint.get("db_type") == "service":
        return await _dispatch_service(endpoint, body, db, full_path, _user_email(request))

    op           = effective_op(endpoint)
    cfg          = endpoint.get("config") or {}
    translations = body.get("translations") or None

    is_jsonb = _is_jsonb_fn(endpoint)

    try:
        # PG-function write: bypass SQL builder, call the registered function directly.
        if endpoint.get("db_type") == "function" and op != "select":
            result = await execute_function_write(db, endpoint, body, _user_email(request))
            return ok(result)

        if op == "insert":
            data = body.get("data") or {}
            row  = await execute_post(db, endpoint, data, translations)
            return ok(row)

        if op == "update":
            patch_data = body.get("patch_data") or {}
            filters    = body.get("filters")    or {}
            rows = await execute_patch(db, endpoint, patch_data, filters, translations)
            return ok(rows)

        if op == "delete":
            filters = body.get("filters") or {}
            deleted = await execute_delete(db, endpoint, filters)
            return ok({"deleted": deleted})

        # select
        if is_jsonb:
            result = await call_jsonb_function(db, endpoint, body)
            return result if cfg.get("raw_response") else ok(result)

        filters = body.get("filters") or {}
        limit   = int(body.get("limit", cfg.get("default_limit") or 100))
        offset  = int(body.get("offset", 0))
        rows = await execute_get(db, endpoint, filters, limit, offset)
        return ok(rows)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _raise_db_error(exc, full_path, endpoint)


# ---------------------------------------------------------------------------
# PATCH — partial UPDATE
# ---------------------------------------------------------------------------

@router.patch("/{full_path:path}")
async def run_patch(
    full_path: str,
    request:   Request,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    endpoint = await _get_active(db, full_path, "PATCH")
    _validate_headers(endpoint, request)
    await _validate_resource_auth(endpoint, request, db)

    if endpoint.get("db_type") == "service":
        return await _dispatch_service(endpoint, body, db, full_path, _user_email(request))

    patch_data   = body.get("patch_data") or {}
    filters      = body.get("filters")    or {}
    translations = body.get("translations") or None

    try:
        if endpoint.get("db_type") == "function":
            result = await execute_function_write(db, endpoint, body, _user_email(request))
            return ok(result)

        rows = await execute_patch(db, endpoint, patch_data, filters, translations)
        return ok(rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _raise_db_error(exc, full_path)


# ---------------------------------------------------------------------------
# DELETE — remove rows
# ---------------------------------------------------------------------------

@router.delete("/{full_path:path}")
async def run_delete(
    full_path: str,
    request:   Request,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    endpoint = await _get_active(db, full_path, "DELETE")
    _validate_headers(endpoint, request)
    await _validate_resource_auth(endpoint, request, db)

    if endpoint.get("db_type") == "service":
        return await _dispatch_service(endpoint, body, db, full_path, _user_email(request))

    filters = body.get("filters") or {}

    try:
        if endpoint.get("db_type") == "function":
            result = await execute_function_write(db, endpoint, body, _user_email(request))
            return ok(result)

        deleted = await execute_delete(db, endpoint, filters)
        return ok({"deleted": deleted})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _raise_db_error(exc, full_path)
