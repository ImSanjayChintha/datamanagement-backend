"""
Module: toolkit.services.page_def_service
Purpose: CRUD for toolkit.page_definitions — dynamic page configurations.
"""
import json
import logging

import asyncpg

from app.core.database import row, rows, execute

logger = logging.getLogger(__name__)


async def list_page_defs(
    db: asyncpg.Connection,
    nav_section: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    page      = max(1, page)
    page_size = max(1, min(200, page_size))
    offset    = (page - 1) * page_size
    like      = f"%{search.lower()}%" if search else None

    total_row = await db.fetchrow(
        """SELECT COUNT(*) AS n FROM toolkit.page_definitions
           WHERE ($1::text IS NULL OR nav_section = $1)
             AND ($2::bool IS NULL OR is_active = $2)
             AND ($3::text IS NULL OR (
                   lower(code)           LIKE $3 OR
                   lower(title)          LIKE $3 OR
                   lower(nav_section)    LIKE $3 OR
                   lower(gateway_object) LIKE $3 OR
                   lower(table_code)     LIKE $3
             ))""",
        nav_section, is_active, like,
    )
    total = total_row["n"] if total_row else 0

    data = await rows(
        db,
        """SELECT * FROM toolkit.page_definitions
           WHERE ($1::text IS NULL OR nav_section = $1)
             AND ($2::bool IS NULL OR is_active = $2)
             AND ($3::text IS NULL OR (
                   lower(code)           LIKE $3 OR
                   lower(title)          LIKE $3 OR
                   lower(nav_section)    LIKE $3 OR
                   lower(gateway_object) LIKE $3 OR
                   lower(table_code)     LIKE $3
             ))
           ORDER BY nav_section, nav_order, code
           LIMIT $4 OFFSET $5""",
        nav_section, is_active, like, page_size, offset,
    )

    pages = max(1, -(-total // page_size))   # ceiling division
    return {"rows": data, "total": total, "page": page, "page_size": page_size, "pages": pages}


async def get_page_def(
    db: asyncpg.Connection,
    id: int | None = None,
    code: str | None = None,
) -> dict:
    if id is not None:
        rec = await row(db, "SELECT * FROM toolkit.page_definitions WHERE id=$1", id)
    elif code is not None:
        rec = await row(db, "SELECT * FROM toolkit.page_definitions WHERE code=$1", code)
    else:
        raise ValueError("id or code required")
    if not rec:
        raise ValueError("Page definition not found")
    return dict(rec)


async def upsert_page_def(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    code             = (data.get("code") or "").strip().lower()
    title            = (data.get("title") or code).strip()
    gateway_object   = (data.get("gateway_object") or "").strip()
    table_code       = (data.get("table_code") or gateway_object).strip()
    id_type          = data.get("id_type") or "string"
    nav_section      = (data.get("nav_section") or "pim").strip()
    nav_label        = data.get("nav_label") or None
    nav_order        = int(data.get("nav_order") or 0)
    sort_order       = int(data.get("sort_order") or 0)
    icon             = data.get("icon") or None
    description      = data.get("description") or None
    is_active        = bool(data.get("is_active", True))
    list_config      = data.get("list_config") or {"columns": []}
    form_config      = data.get("form_config") or {"fields": []}
    list_endpoint    = (data.get("list_endpoint")   or "").strip()
    upsert_endpoint  = (data.get("upsert_endpoint") or "").strip()
    delete_endpoint  = (data.get("delete_endpoint") or "").strip()
    export_endpoint  = (data.get("export_endpoint") or "").strip()

    if not code:
        raise ValueError("code is required")
    if not gateway_object:
        raise ValueError("gateway_object is required")

    rec_id = data.get("id")

    if rec_id:
        result = await row(
            db,
            """UPDATE toolkit.page_definitions
               SET code=$1, title=$2, description=$3, icon=$4,
                   table_code=$5, gateway_object=$6, id_type=$7,
                   nav_section=$8, nav_label=$9, nav_order=$10,
                   list_config=$11::jsonb, form_config=$12::jsonb,
                   is_active=$13, sort_order=$14,
                   list_endpoint=$16, upsert_endpoint=$17, delete_endpoint=$18,
                   modified_at=now(), modified_by=$15
               WHERE id=$19 RETURNING *""",
            code, title, description, icon,
            table_code, gateway_object, id_type,
            nav_section, nav_label, nav_order,
            json.dumps(list_config), json.dumps(form_config),
            is_active, sort_order, user,
            list_endpoint, upsert_endpoint, delete_endpoint, int(rec_id),
        )
    else:
        result = await row(
            db,
            """INSERT INTO toolkit.page_definitions
                   (code, title, description, icon,
                    table_code, gateway_object, id_type,
                    nav_section, nav_label, nav_order,
                    list_config, form_config,
                    is_active, sort_order, inserted_by,
                    list_endpoint, upsert_endpoint, delete_endpoint)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,$15,$16,$17,$18)
               ON CONFLICT (code) DO UPDATE
               SET title=$2, description=$3, icon=$4,
                   table_code=$5, gateway_object=$6, id_type=$7,
                   nav_section=$8, nav_label=$9, nav_order=$10,
                   list_config=$11::jsonb, form_config=$12::jsonb,
                   is_active=$13, sort_order=$14,
                   list_endpoint=$16, upsert_endpoint=$17, delete_endpoint=$18,
                   modified_at=now(), modified_by=$15
               RETURNING *""",
            code, title, description, icon,
            table_code, gateway_object, id_type,
            nav_section, nav_label, nav_order,
            json.dumps(list_config), json.dumps(form_config),
            is_active, sort_order, user,
            list_endpoint, upsert_endpoint, delete_endpoint,
        )

    if not result:
        raise ValueError("Upsert failed")
    return dict(result)


async def delete_page_def(
    db: asyncpg.Connection,
    id: int,
    user: str,
) -> dict:
    rec = await row(db, "SELECT * FROM toolkit.page_definitions WHERE id=$1", id)
    if not rec:
        raise ValueError("Page definition not found")
    await execute(db, "DELETE FROM toolkit.page_definitions WHERE id=$1", id)
    return {"deleted": True, "code": rec["code"]}
