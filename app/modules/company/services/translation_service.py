"""
Module: company.services.translation_service
Purpose: Business logic for company_translation — app-level UI/content
         translations owned by the company.

Reads always go through the toolkit-generated view and functions
(toolkit.v_company_translation, toolkit.fn_list_company_translation,
toolkit.fn_get_company_translation).
Writes target company.company_translation directly.
"""
import logging

import asyncpg

from app.core.database import row, rows, execute, val
from app.modules.company.core.constants import COMPANY_SCHEMA, TABLE_TRANSLATION

logger = logging.getLogger(__name__)

_TABLE = f"{COMPANY_SCHEMA}.{TABLE_TRANSLATION}"
_VIEW  = f"{COMPANY_SCHEMA}.v_{TABLE_TRANSLATION}"


async def list_translations(
    db: asyncpg.Connection,
    search: str | None = None,
    is_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    return await rows(
        db,
        f"""SELECT * FROM {_VIEW}
            WHERE ($1::text IS NULL OR code ILIKE '%'||$1||'%' OR trans_key ILIKE '%'||$1||'%')
              AND ($2::boolean IS NULL OR is_active = $2)
            ORDER BY sort_order, code
            LIMIT $3 OFFSET $4""",
        search, is_active, limit, offset,
    )


async def get_translation(db: asyncpg.Connection, id: int) -> dict:
    result = await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)
    if not result:
        raise ValueError("Translation not found")
    return result


async def create_translation(
    db: asyncpg.Connection,
    data: dict,
    user: str,
) -> dict:
    code      = (data.get("code") or "").strip()
    lang_code = (data.get("lang_code") or "").strip()
    trans_key = (data.get("trans_key") or "").strip()
    value     = (data.get("value") or "").strip()

    if not code:
        raise ValueError("code is required")
    if not lang_code:
        raise ValueError("lang_code is required")
    if not trans_key:
        raise ValueError("trans_key is required")
    if not value:
        raise ValueError("value is required")

    if await val(db, f"SELECT id FROM {_TABLE} WHERE code=$1", code):
        raise ValueError(f"Translation '{code}' already exists")

    new_id = await val(
        db,
        f"""INSERT INTO {_TABLE}
                (code, sort_order, lang_code, module, trans_key, value, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id""",
        code,
        int(data.get("sort_order", 0)),
        lang_code,
        data.get("module") or None,
        trans_key,
        value,
        bool(data.get("is_active", True)),
    )
    return await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", new_id)


async def update_translation(
    db: asyncpg.Connection,
    id: int,
    data: dict,
    user: str,
) -> dict:
    existing = await get_translation(db, id)

    await execute(
        db,
        f"""UPDATE {_TABLE}
            SET code=$1, lang_code=$2, module=$3, trans_key=$4,
                value=$5, is_active=$6, sort_order=$7
            WHERE id=$8""",
        data.get("code",       existing["code"]),
        data.get("lang_code",  existing["lang_code"]),
        data.get("module",     existing.get("module")),
        data.get("trans_key",  existing["trans_key"]),
        data.get("value",      existing["value"]),
        bool(data.get("is_active", existing["is_active"])),
        int(data.get("sort_order", existing["sort_order"])),
        id,
    )
    return await row(db, f"SELECT * FROM {_VIEW} WHERE id=$1", id)


async def delete_translation(
    db: asyncpg.Connection,
    id: int,
    soft: bool = True,
) -> dict:
    await get_translation(db, id)  # raises if not found
    if soft:
        await execute(db, f"UPDATE {_TABLE} SET is_active=FALSE WHERE id=$1", id)
    else:
        await execute(db, f"DELETE FROM {_TABLE} WHERE id=$1", id)
    return {"deleted": True, "soft": soft, "id": id}
