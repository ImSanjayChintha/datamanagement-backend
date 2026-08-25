"""
Sync Excel builders for Celery export/template jobs (psycopg2 + openpyxl).
Mirrors async export_service filtering rules.
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from openpyxl import Workbook

from app.core.config import settings
from app.modules.dbtoolkit.core.constants import AUDIT_FIELD_CODES

_PRODUCT_TEMPLATE_SKIP: frozenset[str] = frozenset(
    set(AUDIT_FIELD_CODES)
    | {
        "id",
        "values",
        "family_code",
        "launched_at",
        "discontinued_at",
        "created_at",
        "updated_at",
    }
)


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value.strip() else {}
    return dict(value)


def _safe_ident(value: str, kind: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid {kind}: {value!r}")
    return value


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not cleaned:
        raise ValueError("family_code produces an empty filename")
    return cleaned


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _headers_from_payload(payload: dict) -> list[str]:
    product_cols = [
        str(c)
        for c in (payload.get("columns") or [])
        if str(c) not in _PRODUCT_TEMPLATE_SKIP
    ]
    attr_cols = list(payload.get("attribute_columns") or [])
    attr_headers = [
        str(c.get("code"))
        for c in attr_cols
        if isinstance(c, dict) and c.get("code") and str(c.get("code")) not in _PRODUCT_TEMPLATE_SKIP
    ]
    headers = product_cols + attr_headers
    if not headers:
        raise ValueError("No columns returned for export")
    return headers


def _load_endpoint_sync(url_path: str, method: str = "POST") -> dict:
    method = method.upper()
    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM api_gateway.endpoints "
                "WHERE url_path = %s AND method = %s AND status = 'active'",
                (url_path, method),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError(f"No active {method} endpoint at {url_path}")
        return dict(row)
    finally:
        conn.close()


def _call_export_fn_sync(family_code: str, endpoint_path: str) -> dict:
    family_code = (family_code or "").strip()
    endpoint_path = (endpoint_path or "").strip()
    if not family_code:
        raise ValueError("family_code is required")
    if not endpoint_path:
        raise ValueError("endpoint is required")

    ep = _load_endpoint_sync(endpoint_path, "POST")
    if (ep.get("db_type") or "") != "function":
        raise ValueError(f"Endpoint {endpoint_path} must be db_type=function")

    schema = _safe_ident(str(ep.get("db_schema") or ""), "db_schema")
    fn_name = _safe_ident(str(ep.get("db_object") or ""), "db_object")

    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'SELECT "{schema}"."{fn_name}"(p_family_code => %s) AS result',
                (family_code,),
            )
            row = cur.fetchone()
        if not row or row["result"] is None:
            raise ValueError("Export function returned no data")
        return _as_dict(row["result"])
    finally:
        conn.close()


def build_template_bytes(family_code: str, endpoint_path: str) -> tuple[bytes, str, int]:
    """Returns (xlsx_bytes, filename, header_count)."""
    payload = _call_export_fn_sync(family_code, endpoint_path)
    headers = _headers_from_payload(payload)
    safe_family = _safe_filename_part(family_code.strip())

    wb = Workbook()
    ws = wb.active
    ws.title = safe_family[:31]
    ws.append(headers)
    buf = BytesIO()
    wb.save(buf)
    filename = f"products-{safe_family}-template.xlsx"
    return buf.getvalue(), filename, len(headers)


def build_data_export_bytes(family_code: str, endpoint_path: str) -> tuple[bytes, str, int]:
    """Returns (xlsx_bytes, filename, data_row_count)."""
    payload = _call_export_fn_sync(family_code, endpoint_path)
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
    return buf.getvalue(), filename, len(data_rows)
