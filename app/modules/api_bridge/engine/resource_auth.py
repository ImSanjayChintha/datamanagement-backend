"""Validates incoming engine requests against the auth config of the linked API resource.

When toolkit.api_objects.resource_code is set, every request for that object
must carry the credentials defined in api_gateway.api_resources.auth_config.
"""
from __future__ import annotations

import base64

import asyncpg
from fastapi import Request

from .errors import unauthorized
from .registry import ObjectMeta


async def validate_resource_auth(
    obj: ObjectMeta,
    request: Request,
    db: asyncpg.Connection,
) -> None:
    """Raise EngineError(401) if the request does not satisfy the resource auth policy.

    No-ops when the object has no linked resource or the resource auth_type is 'none'.
    """
    if not obj.resource_code:
        return

    resource = await db.fetchrow(
        """
        SELECT auth_type, auth_config
        FROM   api_gateway.api_resources
        WHERE  code = $1 AND is_active = TRUE
        """,
        obj.resource_code,
    )
    if not resource:
        return  # resource removed/inactive — don't block the request

    auth_type = (resource["auth_type"] or "none").lower()
    cfg: dict  = dict(resource["auth_config"] or {})

    if auth_type == "none":
        return

    headers = dict(request.headers)

    if auth_type == "bearer":
        prefix   = cfg.get("prefix") or "Bearer"
        token    = cfg.get("token") or ""
        if not token:
            return
        auth_hdr = headers.get("authorization", "")
        if auth_hdr != f"{prefix} {token}":
            raise unauthorized("Invalid or missing bearer token")

    elif auth_type == "basic":
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        if not username:
            return
        auth_hdr = headers.get("authorization", "")
        if not auth_hdr.startswith("Basic "):
            raise unauthorized("Basic authentication required")
        try:
            decoded       = base64.b64decode(auth_hdr[6:]).decode()
            req_user, req_pwd = decoded.split(":", 1)
        except Exception:
            raise unauthorized("Invalid basic auth encoding")
        if req_user != username or req_pwd != password:
            raise unauthorized("Invalid credentials")

    elif auth_type == "api_key":
        key_name  = cfg.get("key_name") or ""
        key_value = cfg.get("key_value") or ""
        location  = cfg.get("location", "header")
        if not key_name or not key_value:
            return
        if location == "header":
            provided = headers.get(key_name.lower(), "")
            if provided != key_value:
                raise unauthorized(f"Invalid or missing {key_name} header")
        elif location == "query":
            provided = request.query_params.get(key_name, "")
            if provided != key_value:
                raise unauthorized(f"Invalid or missing {key_name} query parameter")

    # oauth2: token exchange is outbound-only — not validated on inbound requests
