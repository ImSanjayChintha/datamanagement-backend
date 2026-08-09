"""
Gateway passthrough router.

Allows frontend to call gateway-defined endpoints directly at:
    POST /api/v1/gateway/{path}

without the /run/ prefix, and without hitting the engine router.

Endpoint lookup: url_path = '/gateway/{path}' in api_gateway.endpoints.
Method fallback: tries POST → DELETE → PATCH so that delete/patch endpoints
                 can also be called via POST (frontend always uses POST).

Body format (smart mapping):
  • If body contains a "filters" key  →  pass it directly to execute_get/delete.
  • Otherwise (flat body)             →  auto-map flat keys that match registered
                                         filter column names.
  For function-write endpoints with a flat body (no "data" key), the entire body
  is wrapped as {"data": [body]} before calling execute_function_write.
"""
from __future__ import annotations

import logging

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

router = APIRouter(prefix="/gateway", tags=["Gateway Runtime"])


_SQLSTATE_HTTP: dict[str, tuple[int, str]] = {
    "42703": (422, "unknown field"),
    "42501": (403, "field not filterable"),
    "22023": (422, "invalid filter"),
    "22P02": (422, "invalid value for field type"),
    "42P01": (404, "unknown entity"),
    "P0002": (404, "not found"),
    "42883": (500, "function does not exist"),
}


def _user_email(request: Request) -> str | None:
    return getattr(request.state, "user_email", None)


def _is_jsonb_fn(endpoint: dict) -> bool:
    return (endpoint.get("db_type") == "function"
            and (endpoint.get("config") or {}).get("returns") == "jsonb")


def _raise_db_error(exc: Exception, path: str) -> None:
    if isinstance(exc, asyncpg.PostgresError):
        mapped = _SQLSTATE_HTTP.get(getattr(exc, "sqlstate", None))
        if mapped:
            status, kind = mapped
            logger.warning(
                "DB %s on gateway/%s: %s | ctx=%s",
                getattr(exc, "sqlstate", "?"), path,
                getattr(exc, "message", ""),
                getattr(exc, "context", ""),
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
            "DB error on gateway/%s: [%s] %s | detail=%s | hint=%s",
            path,
            getattr(exc, "sqlstate", "?"),
            getattr(exc, "message", ""),
            getattr(exc, "detail", None),
            getattr(exc, "hint", None),
        )
        raise HTTPException(
            status_code=500,
            detail=f"DB error on gateway/{path}: {getattr(exc, 'message', '')}",
        )
    raise exc


def _extract_filters(body: dict, endpoint: dict) -> tuple[dict, int, int]:
    """
    Extract filters, limit, offset from the request body.

    Supports two body shapes:
      1. Structured: {filters: {...}, limit: N, offset: N}   ← preferred
      2. Flat:       {id: 5, limit: N, skip: N}
         → auto-maps keys that match registered filter column names.
    """
    limit  = int(body.get("limit", 50))
    offset = int(body.get("offset") or body.get("skip", 0))

    if "filters" in body:
        return dict(body.get("filters") or {}), limit, offset

    filter_cols = {f["column"] for f in (endpoint.get("filters") or [])}
    skip_keys   = {"limit", "offset", "skip", "lang", "translations"}
    filters = {k: v for k, v in body.items() if k in filter_cols and k not in skip_keys}
    return filters, limit, offset


@router.post("/{full_path:path}")
async def gateway_direct_post(
    full_path: str,
    request:   Request,
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
):
    """
    Serve a gateway-configured endpoint via POST /api/v1/gateway/{path}.
    Registered before the engine router so it takes priority.
    """
    url_path = f"/gateway/{full_path}"

    # Try POST first; fall back to DELETE / PATCH for endpoints that use those methods
    endpoint = await load_endpoint(db, url_path, "POST")
    if not endpoint:
        endpoint = await load_endpoint(db, url_path, "DELETE")
    if not endpoint:
        endpoint = await load_endpoint(db, url_path, "PATCH")
    if not endpoint:
        raise HTTPException(
            status_code=404,
            detail=f"No active endpoint at /api/v1/gateway/{full_path}",
        )

    logger.info(
        "gateway/%s — db_type=%s schema=%s obj=%s op=%s",
        full_path,
        endpoint.get("db_type"),
        endpoint.get("db_schema"),
        endpoint.get("db_object"),
        effective_op(endpoint),
    )

    from app.modules.api_bridge.gateway.routers.runtime import (
        _validate_headers,
        _validate_resource_auth,
        _dispatch_service,
    )

    _validate_headers(endpoint, request)
    await _validate_resource_auth(endpoint, request, db)

    if endpoint.get("db_type") == "service":
        return await _dispatch_service(endpoint, body, db, full_path)

    op           = effective_op(endpoint)
    cfg          = endpoint.get("config") or {}
    translations = body.get("translations") or None

    try:
        # ── Function write (insert / update / upsert / delete via PG fn) ──────
        if endpoint.get("db_type") == "function" and op != "select":
            if op != "delete" and "data" not in body:
                body = {"data": [
                    {k: v for k, v in body.items()
                     if k not in ("limit", "offset", "skip", "lang", "translations")}
                ]}
            result = await execute_function_write(db, endpoint, body, _user_email(request))
            return ok(result)

        # ── SELECT ────────────────────────────────────────────────────────────
        if op == "select":
            if _is_jsonb_fn(endpoint):
                result = await call_jsonb_function(db, endpoint, body)
                return result if cfg.get("raw_response") else ok(result)
            filters, limit, offset = _extract_filters(body, endpoint)
            rows = await execute_get(db, endpoint, filters, limit, offset)
            return ok(rows if isinstance(rows, list) else [rows])

        # ── INSERT ────────────────────────────────────────────────────────────
        if op == "insert":
            data = body.get("data")
            if data is None:
                skip_keys = {"limit", "offset", "skip", "lang", "translations"}
                data = {k: v for k, v in body.items() if k not in skip_keys}
            row = await execute_post(db, endpoint, data, translations)
            return ok(row)

        # ── UPDATE ────────────────────────────────────────────────────────────
        if op == "update":
            filters, _, _ = _extract_filters(body, endpoint)
            patch_data = body.get("patch_data") or {}
            rows = await execute_patch(db, endpoint, patch_data, filters, translations)
            return ok(rows)

        # ── DELETE ────────────────────────────────────────────────────────────
        if op == "delete":
            filters, _, _ = _extract_filters(body, endpoint)
            deleted = await execute_delete(db, endpoint, filters)
            return ok({"deleted": deleted})

        # Fallback: treat as select
        if _is_jsonb_fn(endpoint):
            result = await call_jsonb_function(db, endpoint, body)
            return result if cfg.get("raw_response") else ok(result)
        filters, limit, offset = _extract_filters(body, endpoint)
        rows = await execute_get(db, endpoint, filters, limit, offset)
        return ok(rows if isinstance(rows, list) else [rows])

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _raise_db_error(exc, full_path)
