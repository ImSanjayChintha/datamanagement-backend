"""
Module: company.services.brand_service
Purpose: Business logic for company_brands — brand catalog.

Reads always go through the toolkit-generated view and functions
(toolkit.v_company_brands, toolkit.fn_list_company_brands,
toolkit.fn_get_company_brands).
Writes target company.company_brands directly.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute, val
from app.modules.company.core.constants import COMPANY_SCHEMA, TABLE_BRANDS

logger = logging.getLogger(__name__)

_TABLE = f"{COMPANY_SCHEMA}.{TABLE_BRANDS}"
_VIEW  = f"{COMPANY_SCHEMA}.v_{TABLE_BRANDS}"


async def list_brands(
    db: asyncpg.Connection,
    search: str | None = None,
    is_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    return await rows(
        db,
        f"""SELECT * FROM {_VIEW}
            WHERE ($1::text IS NULL OR label ILIKE '%'||$1||'%' OR code ILIKE '%'||$1||'%')
              AND ($2::boolean IS NULL OR is_active = $2)
            ORDER BY sort_order, code
            LIMIT $3 OFFSET $4""",
        search, is_active, limit, offset,
    )


async def get_brand(db: asyncpg.Connection, id: int) -> dict:
    result = await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)
    if not result:
        raise ValueError("Brand not found")
    return result


async def create_brand(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()

    if not code:
        raise ValueError("code is required")
    if not name:
        raise ValueError("name is required")

    if await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", code):
        raise ValueError(f"Brand '{code}' already exists")

    new_id = await val(
        db,
        f"""INSERT INTO {_TABLE}
                (code, sort_order, name, description, logo_url, website_url, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id""",
        code,
        int(data.get("sort_order", 0)),
        name,
        data.get("description") or None,
        data.get("logo_url") or None,
        data.get("website_url") or None,
        bool(data.get("is_active", True)),
    )
    return await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", new_id)


async def update_brand(
    db: asyncpg.Connection,
    id: int,
    data: dict,
    user: str,
) -> dict:
    existing = await get_brand(db, id)

    await execute(
        db,
        f"""UPDATE {_TABLE}
            SET code=$1, name=$2, description=$3, logo_url=$4,
                website_url=$5, is_active=$6, sort_order=$7
            WHERE id=$8""",
        data.get("code",        existing["code"]),
        data.get("name",        existing["name"]),
        data.get("description", existing.get("description")),
        data.get("logo_url",    existing.get("logo_url")),
        data.get("website_url", existing.get("website_url")),
        bool(data.get("is_active", existing["is_active"])),
        int(data.get("sort_order", existing["sort_order"])),
        id,
    )
    return await row(db, f"SELECT * FROM {_GET}($1::bigint)", id)


async def delete_brand(
    db: asyncpg.Connection,
    id: int,
    soft: bool = True,
) -> dict:
    await get_brand(db, id)  # raises if not found
    if soft:
        await execute(db, f"UPDATE {_TABLE} SET is_active=FALSE WHERE id=$1", id)
    else:
        await execute(db, f"DELETE FROM {_TABLE} WHERE id=$1", id)
    return {"deleted": True, "soft": soft, "id": id}
