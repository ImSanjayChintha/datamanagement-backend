"""
Thin reverse-proxy for product import / export / template job APIs.

Heavy work (DuckDB, Celery, Excel) runs in jobs-service. This backend only
exposes the same public paths under /api/v1 and forwards the request.
"""
from __future__ import annotations

import logging
from typing import Iterable

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Toolkit - Jobs"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _filter_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers:
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


async def _forward(request: Request, suffix: str) -> Response:
    base = settings.JOBS_SERVICE_URL.rstrip("/")
    target = f"{base}/api/v1/{suffix.lstrip('/')}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    body = await request.body()
    headers = _filter_headers(request.headers.items())

    timeout = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                target,
                headers=headers,
                content=body if body else None,
            ),
            stream=True,
        )
    except httpx.RequestError as exc:
        await client.aclose()
        logger.error("jobs-service unreachable url=%s err=%s", target, exc)
        return Response(
            content='{"success":false,"data":null,"error":"Jobs service unavailable"}',
            status_code=502,
            media_type="application/json",
        )

    media = upstream.headers.get("content-type", "application/octet-stream")
    out_headers = _filter_headers(upstream.headers.items())
    disposition = upstream.headers.get("content-disposition", "")

    # SSE and file downloads — stream through
    stream = (
        "text/event-stream" in media
        or "octet-stream" in media
        or "spreadsheet" in media
        or "attachment" in disposition.lower()
    )
    if stream:
        async def _stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            headers=out_headers,
            media_type=media,
        )

    content = await upstream.aread()
    await upstream.aclose()
    await client.aclose()
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=media,
    )


@router.api_route(
    "/admin/toolkit/import",
    methods=_METHODS,
    include_in_schema=True,
)
@router.api_route(
    "/admin/toolkit/import/{path:path}",
    methods=_METHODS,
    include_in_schema=True,
)
async def proxy_import(request: Request, path: str = "") -> Response:
    suffix = f"admin/toolkit/import/{path}" if path else "admin/toolkit/import"
    return await _forward(request, suffix)


@router.api_route(
    "/admin/toolkit/export",
    methods=_METHODS,
    include_in_schema=True,
)
@router.api_route(
    "/admin/toolkit/export/{path:path}",
    methods=_METHODS,
    include_in_schema=True,
)
async def proxy_export(request: Request, path: str = "") -> Response:
    suffix = f"admin/toolkit/export/{path}" if path else "admin/toolkit/export"
    return await _forward(request, suffix)
