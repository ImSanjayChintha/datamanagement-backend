"""
Module: toolkit.services.export
Purpose: Build Excel templates/data dumps using gateway-registered SQL functions.
         Function name comes from api_gateway.endpoints (same pattern as runtime).
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Any

import asyncpg
from openpyxl import Workbook

from app.modules.api_bridge.gateway.runtime.executor import load_endpoint


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value.strip() else {}
    return dict(value)


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not cleaned:
        raise ValueError("family_code produces an empty filename")
    return cleaned


def _safe_ident(value: str, kind: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid {kind}: {value!r}")
    return value


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


async def _call_export_fn(
    db: asyncpg.Connection,
    family_code: str,
    endpoint_path: str,
) -> dict:
    family_code = (family_code or "").strip()
    if not family_code:
        raise ValueError("family_code is required")

    endpoint_path = (endpoint_path or "").strip()
    if not endpoint_path:
        raise ValueError("endpoint is required")
    if not endpoint_path.startswith("/"):
        raise ValueError("endpoint must start with '/'")

    endpoint = await load_endpoint(db, endpoint_path, "POST")
    if not endpoint:
        raise ValueError(f"No active POST endpoint at {endpoint_path}")
    if endpoint.get("db_type") != "function":
        raise ValueError(f"Endpoint {endpoint_path} must be db_type=function")

    schema = _safe_ident(str(endpoint.get("db_schema") or ""), "db_schema")
    fn_name = _safe_ident(str(endpoint.get("db_object") or ""), "db_object")

    row = await db.fetchrow(
        f'SELECT "{schema}"."{fn_name}"(p_family_code => $1) AS result',
        family_code,
    )
    if not row or row["result"] is None:
        raise ValueError("Export function returned no data")

    return _as_dict(row["result"])


def _headers_from_payload(payload: dict) -> list[str]:
    product_cols = list(payload.get("columns") or [])
    attr_cols = list(payload.get("attribute_columns") or [])
    attr_headers = [
        str(c.get("code"))
        for c in attr_cols
        if isinstance(c, dict) and c.get("code")
    ]
    headers = [str(c) for c in product_cols] + attr_headers
    if not headers:
        raise ValueError("No columns returned for export")
    return headers


async def build_template(
    db: asyncpg.Connection,
    family_code: str,
    endpoint_path: str,
) -> tuple[bytes, str]:
    payload = await _call_export_fn(db, family_code, endpoint_path)
    headers = _headers_from_payload(payload)
    safe_family = _safe_filename_part(family_code.strip())

    wb = Workbook()
    ws = wb.active
    ws.title = safe_family[:31]
    ws.append(headers)

    buf = BytesIO()
    wb.save(buf)

    filename = f"products-{safe_family}-template.xlsx"
    return buf.getvalue(), filename


async def build_data_export(
    db: asyncpg.Connection,
    family_code: str,
    endpoint_path: str,
) -> tuple[bytes, str]:
    payload = await _call_export_fn(db, family_code, endpoint_path)
    headers = _headers_from_payload(payload)
    data_rows = list(payload.get("rows") or [])
    safe_family = _safe_filename_part(family_code.strip())

    wb = Workbook()
    ws = wb.active
    ws.title = safe_family[:31]
    ws.append(headers)

    for item in data_rows:
        if not isinstance(item, dict):
            item = _as_dict(item)
        ws.append([_cell(item.get(h)) for h in headers])

    buf = BytesIO()
    wb.save(buf)

    filename = f"products-{safe_family}-export.xlsx"
    return buf.getvalue(), filename