import asyncio
import json
import logging
import ssl

import asyncpg
import httpx
from fastapi import APIRouter, Body, Depends

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/push-destinations", tags=["Push Destinations"])

# Secret field names per destination type — never returned by the API
_SECRET_FIELDS: dict[str, list[str]] = {
    "azure_blob":   ["sas_token", "access_key"],
    "email":        [],
    "s3_compatible": ["secret_access_key"],
    "amazon_s3":    ["secret_access_key"],
    "filesystem":   [],
    "rabbitmq":     ["password"],
    "sftp":         ["password"],
    "http_api":     ["auth_value", "auth_username"],
}

VALID_TYPES = {"azure_blob", "email", "s3_compatible", "amazon_s3", "filesystem", "rabbitmq", "sftp", "http_api"}


async def _test_tcp(
    host: str,
    port: int,
    label: str,
    use_ssl: bool = False,
    ca_cert_path: str | None = None,
) -> dict:
    if not host:
        return {"ok": False, "message": f"{label}: host is not configured."}
    try:
        ssl_ctx: ssl.SSLContext | bool = False
        if use_ssl:
            ssl_ctx = ssl.create_default_context()
            if ca_cert_path:
                ssl_ctx.load_verify_locations(ca_cert_path)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx or None),
            timeout=8,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        ssl_note = " (TLS)" if use_ssl else ""
        return {"ok": True, "message": f"{label}: TCP connection to {host}:{port}{ssl_note} succeeded."}
    except asyncio.TimeoutError:
        return {"ok": False, "message": f"{label}: Connection to {host}:{port} timed out (8 s)."}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"{label}: Connection to {host}:{port} refused."}
    except ssl.SSLError as exc:
        return {"ok": False, "message": f"{label}: TLS error — {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"{label}: {exc}"}


async def _test_azure_blob(
    account: str,
    auth_method: str,
    sas_token: str,
    endpoint_url: str,
) -> dict:
    account = account.strip()
    if not account:
        return {"ok": False, "message": "Azure Blob: storage account name is not configured."}

    base = endpoint_url.rstrip("/") if endpoint_url.strip() else f"https://{account}.blob.core.windows.net"

    if auth_method in ("sas_token", "access_key") and not sas_token.strip():
        return {"ok": False, "message": "Azure Blob: no credentials configured — save a SAS token or access key before testing."}

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            if auth_method == "sas_token" and sas_token.strip():
                token = sas_token.strip().lstrip("?")
                url = f"{base}/?restype=account&comp=properties&{token}"
                r = await client.get(url)
                if r.status_code == 200:
                    return {"ok": True, "message": f"Azure Blob: authenticated access to '{account}' confirmed."}
                if r.status_code in (401, 403):
                    return {"ok": False, "message": f"Azure Blob: authentication failed — SAS token may be invalid or expired (HTTP {r.status_code})."}
                return {"ok": False, "message": f"Azure Blob: unexpected response from '{account}' — HTTP {r.status_code}."}
            else:
                # access_key or managed_identity — only network reachability is testable without the Azure SDK
                r = await client.head(base)
                if r.status_code < 500:
                    return {
                        "ok": True,
                        "message": (
                            f"Azure Blob: '{account}' endpoint is reachable (HTTP {r.status_code}). "
                            "Credential verification requires the Azure SDK and is not performed here."
                        ),
                    }
                return {"ok": False, "message": f"Azure Blob: '{account}' returned HTTP {r.status_code}."}
    except httpx.ConnectTimeout:
        return {"ok": False, "message": f"Azure Blob: connection to {base} timed out (8 s)."}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Azure Blob: could not reach '{account}' — check the account name or endpoint URL."}
    except Exception as exc:
        return {"ok": False, "message": f"Azure Blob: {exc}"}


async def _test_http(url: str, label: str) -> dict:
    if not url:
        return {"ok": False, "message": f"{label}: URL is not configured."}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            r = await client.head(url)
            return {"ok": True, "message": f"{label}: {url} responded with HTTP {r.status_code}."}
    except httpx.ConnectTimeout:
        return {"ok": False, "message": f"{label}: Connection to {url} timed out (8 s)."}
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"{label}: Could not connect — {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"{label}: {exc}"}


def _row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    d["config"]  = d.get("config")  or {}
    # secrets column is never exposed — drop it completely
    d.pop("secrets", None)
    return d


# ── List ──────────────────────────────────────────────────────────────────────

@router.post("/list")
async def list_destinations(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        page      = max(1, int(body.get("page") or 1))
        page_size = max(1, min(500, int(body.get("page_size") or 50)))
        offset    = (page - 1) * page_size
        search    = (body.get("search") or "").strip() or None
        dest_type = (body.get("dest_type") or "").strip() or None

        where_clauses = []
        args: list = []

        if search:
            args.append(f"%{search}%")
            where_clauses.append(f"(name ILIKE ${len(args)} OR description ILIKE ${len(args)})")

        if dest_type:
            args.append(dest_type)
            where_clauses.append(f"dest_type = ${len(args)}")

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = await db.fetchval(
            f"SELECT COUNT(*) FROM api_gateway.push_destinations {where}",
            *args,
        )

        args_page = args + [page_size, offset]
        rows = await db.fetch(
            f"""
            SELECT id, name, description, dest_type, config, is_active,
                   inserted_at, inserted_by, modified_at, modified_by
            FROM   api_gateway.push_destinations
            {where}
            ORDER  BY name
            LIMIT  ${len(args_page) - 1} OFFSET ${len(args_page)}
            """,
            *args_page,
        )

        pages = max(1, (total + page_size - 1) // page_size)
        return ok({
            "rows":      [_row_to_dict(r) for r in rows],
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     pages,
        })
    except Exception as exc:
        logger.exception("push-destinations list error")
        return err(str(exc))


# ── Get ───────────────────────────────────────────────────────────────────────

@router.post("/get")
async def get_destination(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        dest_id = int(body.get("id") or 0)
        row = await db.fetchrow(
            """
            SELECT id, name, description, dest_type, config, is_active,
                   inserted_at, inserted_by, modified_at, modified_by
            FROM   api_gateway.push_destinations
            WHERE  id = $1
            """,
            dest_id,
        )
        if not row:
            return err("Destination not found")
        return ok(_row_to_dict(row))
    except Exception as exc:
        return err(str(exc))


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("/create")
async def create_destination(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        name      = (body.get("name") or "").strip()
        dest_type = (body.get("dest_type") or "").strip()
        if not name:
            return err("name is required")
        if dest_type not in VALID_TYPES:
            return err(f"dest_type must be one of {sorted(VALID_TYPES)}")

        description = (body.get("description") or "").strip() or None
        config      = body.get("config") or {}
        secrets     = body.get("secrets") or {}
        is_active   = bool(body.get("is_active", True))

        row = await db.fetchrow(
            """
            INSERT INTO api_gateway.push_destinations
                (name, description, dest_type, config, secrets, is_active,
                 inserted_by, modified_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
            RETURNING id, name, description, dest_type, config, is_active,
                      inserted_at, inserted_by, modified_at, modified_by
            """,
            name, description, dest_type,
            json.dumps(config), json.dumps(secrets),
            is_active, admin.get("email"),
        )
        return ok(_row_to_dict(row))
    except Exception as exc:
        logger.exception("push-destinations create error")
        return err(str(exc))


# ── Update ────────────────────────────────────────────────────────────────────

@router.post("/update")
async def update_destination(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        dest_id = int(body.get("id") or 0)
        if not dest_id:
            return err("id is required")

        existing = await db.fetchrow(
            "SELECT * FROM api_gateway.push_destinations WHERE id = $1",
            dest_id,
        )
        if not existing:
            return err("Destination not found")

        name        = (body.get("name") or "").strip() or existing["name"]
        description = body.get("description", existing["description"])
        is_active   = body.get("is_active", existing["is_active"])
        new_config  = body.get("config", None)
        new_secrets = body.get("secrets", None)

        # Merge config (patch, not replace)
        merged_config = dict(existing["config"] or {})
        if isinstance(new_config, dict):
            merged_config.update(new_config)

        # Merge secrets: empty-string values mean "keep existing"
        merged_secrets = dict(existing["secrets"] or {})
        if isinstance(new_secrets, dict):
            for k, v in new_secrets.items():
                if v is not None and v != "":
                    merged_secrets[k] = v

        row = await db.fetchrow(
            """
            UPDATE api_gateway.push_destinations
            SET    name        = $2,
                   description = $3,
                   config      = $4,
                   secrets     = $5,
                   is_active   = $6,
                   modified_at = NOW(),
                   modified_by = $7
            WHERE  id = $1
            RETURNING id, name, description, dest_type, config, is_active,
                      inserted_at, inserted_by, modified_at, modified_by
            """,
            dest_id, name, description,
            json.dumps(merged_config), json.dumps(merged_secrets),
            is_active, admin.get("email"),
        )
        return ok(_row_to_dict(row))
    except Exception as exc:
        logger.exception("push-destinations update error")
        return err(str(exc))


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/delete")
async def delete_destination(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        dest_id = int(body.get("id") or 0)
        if not dest_id:
            return err("id is required")
        await db.execute(
            "DELETE FROM api_gateway.push_destinations WHERE id = $1",
            dest_id,
        )
        return ok({"deleted": True})
    except Exception as exc:
        return err(str(exc))


# ── Test connection ───────────────────────────────────────────────────────────

@router.post("/test")
async def test_destination(
    body:  dict = Body(default={}),
    db:    asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    try:
        dest_id = body.get("id")
        if dest_id:
            row = await db.fetchrow(
                "SELECT dest_type, config, secrets FROM api_gateway.push_destinations WHERE id = $1",
                int(dest_id),
            )
            if not row:
                return err("Destination not found")
            dest_type = row["dest_type"]
            config    = dict(row["config"] or {})
            secrets   = dict(row["secrets"] or {})
        else:
            dest_type = (body.get("dest_type") or "").strip()
            config    = body.get("config") or {}
            secrets   = body.get("secrets") or {}

        if dest_type == "rabbitmq":
            if not config.get("host", "").strip():
                return ok({"ok": False, "message": "RabbitMQ: host is not configured."})
            if not secrets.get("password", "").strip():
                return ok({"ok": False, "message": "RabbitMQ: no password configured — save credentials before testing the connection."})
            return ok(await _test_tcp(
                host=config.get("host", ""),
                port=int(config.get("port") or 5672),
                label="RabbitMQ",
                use_ssl=bool(config.get("use_ssl", False)),
                ca_cert_path=config.get("ca_cert_path") or None,
            ))

        if dest_type == "sftp":
            if not config.get("host", "").strip():
                return ok({"ok": False, "message": "SFTP: host is not configured."})
            if not secrets.get("password", "").strip():
                return ok({"ok": False, "message": "SFTP: no password configured — save credentials before testing the connection."})
            return ok(await _test_tcp(
                host=config.get("host", ""),
                port=int(config.get("port") or 22),
                label="SFTP",
            ))

        if dest_type == "s3_compatible":
            endpoint = config.get("endpoint_url", "").rstrip("/")
            return ok(await _test_http(endpoint, label="S3 Compatible"))

        if dest_type == "amazon_s3":
            region = config.get("region", "us-east-1")
            url    = f"https://s3.{region}.amazonaws.com"
            return ok(await _test_http(url, label="Amazon S3"))

        if dest_type == "http_api":
            return ok(await _test_http(config.get("url", ""), label="HTTP API"))

        if dest_type == "azure_blob":
            return ok(await _test_azure_blob(
                account=config.get("account_name", ""),
                auth_method=config.get("auth_method", ""),
                sas_token=secrets.get("sas_token", ""),
                endpoint_url=config.get("endpoint_url", ""),
            ))

        if dest_type == "filesystem":
            path = config.get("base_path", "")
            return ok({"ok": False, "message": f"File System path '{path}' — connectivity test not available for local filesystem destinations."})

        if dest_type == "email":
            return ok({"ok": False, "message": "Email (SMTP) — connectivity test is not yet implemented. SMTP settings are configured via server environment variables."})

        return ok({"ok": False, "message": f"Connectivity test is not yet implemented for '{dest_type}'."})
    except Exception as exc:
        return err(str(exc))
