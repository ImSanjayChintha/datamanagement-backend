"""
Module: company.services.sector_service
Purpose: Business logic for company_sectors — business sector/industry catalog.

Reads always go through the toolkit-generated view and functions
(toolkit.v_company_sectors, toolkit.fn_list_company_sectors,
toolkit.fn_get_company_sectors).
Writes target company.company_sectors directly.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute, val
from app.modules.company.core.constants import COMPANY_SCHEMA, TABLE_SECTORS

logger = logging.getLogger(__name__)

_TABLE = f"{COMPANY_SCHEMA}.{TABLE_SECTORS}"
_VIEW  = f"{COMPANY_SCHEMA}.v_{TABLE_SECTORS}"


async def list_sectors(
    db: asyncpg.Connection,
    search: str | None = None,
    is_active: bool | None = None,
    parent_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    result = await rows(
        db,
        f"""SELECT * FROM {_VIEW}
            WHERE ($1::text IS NULL OR label ILIKE '%'||$1||'%' OR code ILIKE '%'||$1||'%')
              AND ($2::boolean IS NULL OR is_active = $2)
            ORDER BY sort_order, code
            LIMIT $3 OFFSET $4""",
        search, is_active, limit, offset,
    )
    if parent_code is not None:
        result = [r for r in result if r.get("parent_code") == parent_code]
    return result


async def get_sector(db: asyncpg.Connection, id: int) -> dict:
    result = await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)
    if not result:
        raise ValueError("Sector not found")
    return result


async def create_sector(
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
        raise ValueError(f"Sector '{code}' already exists")

    parent_code = (data.get("parent_code") or "").strip() or None
    if parent_code and not await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", parent_code):
        raise ValueError(f"Parent sector '{parent_code}' not found")

    new_id = await val(
        db,
        f"""INSERT INTO {_TABLE}
                (code, sort_order, name, description, parent_code, is_active)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id""",
        code,
        int(data.get("sort_order", 0)),
        name,
        data.get("description") or None,
        parent_code,
        bool(data.get("is_active", True)),
    )
    return await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", new_id)


async def update_sector(
    db: asyncpg.Connection,
    id: int,
    data: dict,
    user: str,
) -> dict:
    existing = await get_sector(db, id)

    parent_code = data.get("parent_code", existing.get("parent_code"))
    if parent_code:
        parent_code = parent_code.strip() or None
    if parent_code and parent_code != existing.get("parent_code"):
        if not await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", parent_code):
            raise ValueError(f"Parent sector '{parent_code}' not found")
        if parent_code == existing["code"]:
            raise ValueError("A sector cannot be its own parent")

    await execute(
        db,
        f"""UPDATE {_TABLE}
            SET code=$1, name=$2, description=$3, parent_code=$4,
                is_active=$5, sort_order=$6
            WHERE id=$7""",
        data.get("code",        existing["code"]),
        data.get("name",        existing["name"]),
        data.get("description", existing.get("description")),
        parent_code,
        bool(data.get("is_active", existing["is_active"])),
        int(data.get("sort_order", existing["sort_order"])),
        id,
    )
    return await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)


async def delete_sector(
    db: asyncpg.Connection,
    id: int,
    soft: bool = True,
) -> dict:
    existing = await get_sector(db, id)

    children = await rows(
        db, f"SELECT id FROM {_TABLE} WHERE parent_code=$1 AND is_active=TRUE", existing["code"]
    )
    if children:
        raise ValueError("Cannot delete a sector that has active child sectors")

    if soft:
        await execute(db, f"UPDATE {_TABLE} SET is_active=FALSE WHERE id=$1", id)
    else:
        await execute(db, f"DELETE FROM {_TABLE} WHERE id=$1", id)
    return {"deleted": True, "soft": soft, "id": id}
