"""
Module: api_bridge.resources.services.resource_service
Purpose: CRUD and connection testing for API resource connections.
"""
import base64
import json
import logging
import time

import httpx

from app.core.database import execute, row, rows, val

logger = logging.getLogger(__name__)

_TABLE = "api_gateway.api_resources"


async def list_resources(
    db,
    *,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    _where = """
        WHERE ($1::boolean IS NULL OR is_active = $1)
          AND ($2::text IS NULL
               OR name     ILIKE '%'||$2||'%'
               OR code     ILIKE '%'||$2||'%'
               OR base_url ILIKE '%'||$2||'%')
    """
    total = await val(db, f"SELECT COUNT(*) FROM {_TABLE} {_where}", is_active, search)
    data  = await rows(
        db,
        f"SELECT * FROM {_TABLE} {_where} ORDER BY name LIMIT $3 OFFSET $4",
        is_active, search, limit, offset,
    )
    return {"rows": data, "total": total}


async def get_resource(db, resource_id: int) -> dict | None:
    return await row(db, f"SELECT * FROM {_TABLE} WHERE id = $1", resource_id)


async def _get_by_code(db, code: str) -> dict | None:
    return await row(db, f"SELECT * FROM {_TABLE} WHERE code = $1", code)


async def create_resource(db, data: dict, user: str | None = None) -> dict:
    name        = (data.get("name") or "").strip()
    code        = _to_code(data.get("code") or "")
    base_url    = (data.get("base_url") or "").strip().rstrip("/")
    description = (data.get("description") or "").strip() or None
    timeout     = max(1, int(data.get("timeout_seconds") or 30))
    auth_type   = (data.get("auth_type") or "none").lower()
    auth_config = data.get("auth_config") or {}
    def_headers = data.get("default_headers") or {}
    ssl_verify  = bool(data.get("ssl_verify", True))
    is_active   = bool(data.get("is_active", True))

    if not name:
        raise ValueError("name is required")
    if not code:
        raise ValueError("code is required")
    if not base_url:
        raise ValueError("base_url is required")
    _validate_auth_type(auth_type)

    if await _get_by_code(db, code):
        raise ValueError(f"A resource with code '{code}' already exists")

    return await row(
        db,
        f"""
        INSERT INTO {_TABLE}
            (code, name, description, base_url, timeout_seconds,
             auth_type, auth_config, default_headers, ssl_verify, is_active,
             inserted_by, modified_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)
        RETURNING *
        """,
        code, name, description, base_url, timeout,
        auth_type, json.dumps(auth_config), json.dumps(def_headers), ssl_verify, is_active, user,
    )


async def update_resource(db, resource_id: int, data: dict, user: str | None = None) -> dict:
    existing = await get_resource(db, resource_id)
    if not existing:
        raise ValueError("Resource not found")

    name        = (data.get("name") or existing["name"]).strip()
    code        = _to_code(data.get("code") or existing["code"])
    base_url    = (data.get("base_url") or existing["base_url"]).strip().rstrip("/")
    description = data.get("description", existing.get("description"))
    if description is not None:
        description = description.strip() or None
    timeout     = max(1, int(data.get("timeout_seconds") or existing["timeout_seconds"]))
    auth_type   = (data.get("auth_type") or existing["auth_type"]).lower()
    auth_config = data.get("auth_config", existing.get("auth_config") or {})
    def_headers = data.get("default_headers", existing.get("default_headers") or {})
    ssl_verify  = bool(data.get("ssl_verify", existing["ssl_verify"]))
    is_active   = bool(data.get("is_active", existing["is_active"]))

    _validate_auth_type(auth_type)

    if code != existing["code"] and await _get_by_code(db, code):
        raise ValueError(f"A resource with code '{code}' already exists")

    return await row(
        db,
        f"""
        UPDATE {_TABLE}
        SET code=$1, name=$2, description=$3, base_url=$4, timeout_seconds=$5,
            auth_type=$6, auth_config=$7, default_headers=$8,
            ssl_verify=$9, is_active=$10,
            modified_at=NOW(), modified_by=$11
        WHERE id=$12
        RETURNING *
        """,
        code, name, description, base_url, timeout,
        auth_type, json.dumps(auth_config), json.dumps(def_headers), ssl_verify, is_active,
        user, resource_id,
    )


async def delete_resource(db, resource_id: int) -> None:
    if not await get_resource(db, resource_id):
        raise ValueError("Resource not found")
    await execute(db, f"DELETE FROM {_TABLE} WHERE id = $1", resource_id)


async def test_connection(db, resource_id: int) -> dict:
    resource = await get_resource(db, resource_id)
    if not resource:
        raise ValueError("Resource not found")

    base_url    = resource["base_url"]
    ssl_verify  = resource["ssl_verify"]
    timeout_s   = resource["timeout_seconds"]
    auth_type   = resource.get("auth_type", "none")
    auth_cfg    = dict(resource.get("auth_config") or {})
    def_headers = dict(resource.get("default_headers") or {})

    request_headers: dict = {**def_headers}
    params: dict = {}

    if auth_type == "bearer":
        prefix = auth_cfg.get("prefix") or "Bearer"
        token  = auth_cfg.get("token") or ""
        request_headers["Authorization"] = f"{prefix} {token}"

    elif auth_type == "basic":
        username = auth_cfg.get("username") or ""
        password = auth_cfg.get("password") or ""
        encoded  = base64.b64encode(f"{username}:{password}".encode()).decode()
        request_headers["Authorization"] = f"Basic {encoded}"

    elif auth_type == "api_key":
        key_name  = auth_cfg.get("key_name") or ""
        key_value = auth_cfg.get("key_value") or ""
        if auth_cfg.get("location") == "query":
            if key_name:
                params[key_name] = key_value
        elif key_name:
            request_headers[key_name] = key_value

    elif auth_type == "oauth2":
        token_url     = auth_cfg.get("token_url") or ""
        client_id     = auth_cfg.get("client_id") or ""
        client_secret = auth_cfg.get("client_secret") or ""
        scope         = auth_cfg.get("scope") or ""
        grant_type    = auth_cfg.get("grant_type") or "client_credentials"
        if not token_url:
            return {
                "success": False,
                "status_code": None,
                "message": "OAuth2 token_url is not configured",
                "response_time_ms": None,
            }
        try:
            async with httpx.AsyncClient(verify=ssl_verify, timeout=10) as client:
                token_resp = await client.post(
                    token_url,
                    data={
                        "grant_type": grant_type,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": scope,
                    },
                )
            if token_resp.status_code == 200:
                token_data = token_resp.json()
                access_token = token_data.get("access_token", "")
                request_headers["Authorization"] = f"Bearer {access_token}"
            else:
                return {
                    "success": False,
                    "status_code": token_resp.status_code,
                    "message": f"OAuth2 token fetch failed — HTTP {token_resp.status_code}",
                    "response_time_ms": None,
                }
        except httpx.ConnectError as exc:
            return {
                "success": False,
                "status_code": None,
                "message": f"Cannot reach OAuth2 token URL: {exc}",
                "response_time_ms": None,
            }
        except Exception as exc:
            return {
                "success": False,
                "status_code": None,
                "message": f"OAuth2 token fetch error: {exc}",
                "response_time_ms": None,
            }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            verify=ssl_verify,
            timeout=timeout_s,
            follow_redirects=True,
        ) as client:
            resp = await client.get(base_url, headers=request_headers, params=params)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "success": True,
            "status_code": resp.status_code,
            "message": f"Reachable — HTTP {resp.status_code} in {elapsed_ms}ms",
            "response_time_ms": elapsed_ms,
        }

    except httpx.ConnectError as exc:
        return {
            "success": False,
            "status_code": None,
            "message": f"Connection refused: {exc}",
            "response_time_ms": None,
        }
    except httpx.TimeoutException:
        return {
            "success": False,
            "status_code": None,
            "message": f"Request timed out after {timeout_s}s",
            "response_time_ms": None,
        }
    except Exception as exc:
        logger.exception("test_connection error for resource %s", resource_id)
        return {
            "success": False,
            "status_code": None,
            "message": str(exc),
            "response_time_ms": None,
        }


def _to_code(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "_", value.strip().lower()).strip("_")


def _validate_auth_type(auth_type: str) -> None:
    from app.modules.api_bridge.resources.core.constants import AUTH_TYPES
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"auth_type must be one of: {', '.join(sorted(AUTH_TYPES))}")
