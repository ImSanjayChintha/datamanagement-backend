"""
Module: company.services.language_service
Purpose: Business logic for company_languages — language catalog.

Reads go through fn_list_company_languages (thin toolkit wrapper) or the
base table directly. Writes target company.company_languages directly.
"""
import json
import logging

import asyncpg

from app.core.database import row, execute, val
from app.modules.company.core.constants import COMPANY_SCHEMA

logger = logging.getLogger(__name__)

_TABLE_CODE = "company_languages"
_TABLE      = f"{COMPANY_SCHEMA}.{_TABLE_CODE}"


async def list_languages(db: asyncpg.Connection) -> list[dict]:
    raw = await db.fetchval(
        "SELECT company.fn_list_company_languages("
        "    p_limit      := 1000,"
        "    p_with_total := false"
        ")"
    )
    data = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    if isinstance(data, list):
        return data
    return data.get("rows", [])


async def get_language(db: asyncpg.Connection, id: int) -> dict:
    result = await row(db, f"SELECT * FROM {_TABLE} WHERE id=$1", id)
    if not result:
        raise ValueError("Language not found")
    return result


async def create_language(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    code = (data.get("code") or "").strip().lower()
    iso3 = (data.get("iso3") or "").strip().lower()
    name = (data.get("name") or "").strip()

    if not code:
        raise ValueError("code is required")
    if not iso3:
        raise ValueError("iso3 is required")
    if not name:
        raise ValueError("name is required")

    if await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", code):
        raise ValueError(f"Language '{code}' already exists")
    if await val(db, f"SELECT id FROM {_TABLE} WHERE iso3=$1", iso3):
        raise ValueError(f"ISO3 code '{iso3}' is already in use")

    new_id = await val(
        db,
        f"""INSERT INTO {_TABLE}
                (code, iso3, iso2, name, flag_icon, is_active)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id""",
        code,
        iso3,
        (data.get("iso2") or "").strip().lower() or None,
        name,
        data.get("flag_icon") or None,
        bool(data.get("is_active", True)),
    )
    return await row(db, f"SELECT * FROM {_TABLE} WHERE id=$1", new_id)


async def update_language(
    db: asyncpg.Connection,
    id: int,
    data: dict,
    user: str,
) -> dict:
    existing = await get_language(db, id)

    new_iso3 = (data.get("iso3") or existing["iso3"]).strip().lower()
    if new_iso3 != existing["iso3"]:
        conflict = await val(db, f"SELECT id FROM {_TABLE} WHERE iso3=$1 AND id!=$2", new_iso3, id)
        if conflict:
            raise ValueError(f"ISO3 code '{new_iso3}' is already in use")

    await execute(
        db,
        f"""UPDATE {_TABLE}
            SET code=$1, iso3=$2, iso2=$3, name=$4, flag_icon=$5, is_active=$6, sort_order=$7
            WHERE id=$8""",
        (data.get("code") or existing["code"]).strip().lower(),
        new_iso3,
        (data.get("iso2") or existing.get("iso2") or "").strip().lower() or None,
        (data.get("name") or existing["name"]).strip(),
        data.get("flag_icon", existing.get("flag_icon")),
        bool(data.get("is_active", existing["is_active"])),
        int(data.get("sort_order", existing["sort_order"])),
        id,
    )
    return await row(db, f"SELECT * FROM {_TABLE} WHERE id=$1", id)


async def delete_language(
    db: asyncpg.Connection,
    id: int,
    soft: bool = True,
) -> dict:
    await get_language(db, id)  # raises if not found
    if soft:
        await execute(db, f"UPDATE {_TABLE} SET is_active=FALSE WHERE id=$1", id)
    else:
        await execute(db, f"DELETE FROM {_TABLE} WHERE id=$1", id)
    return {"deleted": True, "soft": soft, "id": id}
