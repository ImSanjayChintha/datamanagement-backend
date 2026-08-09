"""
Module: toolkit.services.option_service
Purpose: Business logic for toolkit field option management.
         Options apply only to inline_select fields.
"""
import json
import logging

import asyncpg

from app.core.database import row, rows, execute

logger = logging.getLogger(__name__)


async def list_options(db: asyncpg.Connection, field_id: int) -> list:
    """Return all options for an inline_select field.

    Args:
        db: Active database connection.
        field_id: Primary key of the field in toolkit_fields.

    Returns:
        List of option dicts ordered by sort_order then code.
    """
    return await rows(
        db,
        "SELECT * FROM toolkit_field_options WHERE field_id=$1 ORDER BY sort_order, code",
        field_id,
    )


async def create_option(db: asyncpg.Connection, data: dict) -> dict:
    """Create a new option for an inline_select field.

    Args:
        db: Active database connection.
        data: Dict with field_id, code, label, and optional color/sort_order/is_active.

    Returns:
        Created option dict.

    Raises:
        ValueError: If required fields are missing, the field is not found,
            or the field type is not inline_select.
    """
    field_id = data.get("field_id")
    code     = (data.get("code") or "").strip().lower()
    label    = (data.get("label") or "").strip()
    if not field_id or not code or not label:
        raise ValueError("field_id, code, label are required")

    field = await row(db, "SELECT * FROM toolkit_fields WHERE id=$1", field_id)
    if not field:
        raise ValueError("Field not found")
    if field["field_type"] != "inline_select":
        raise ValueError("Options only apply to inline_select fields")

    label_i18n = data.get("label_i18n") or {}
    return await row(
        db,
        """INSERT INTO toolkit_field_options (field_id, code, label, color, sort_order, is_active, label_i18n)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (field_id, code) DO UPDATE SET
               label      = EXCLUDED.label,
               color      = EXCLUDED.color,
               sort_order = EXCLUDED.sort_order,
               is_active  = EXCLUDED.is_active,
               label_i18n = EXCLUDED.label_i18n
           RETURNING *""",
        field_id, code, label,
        data.get("color") or None,
        int(data.get("sort_order", 0)),
        bool(data.get("is_active", True)),
        json.dumps(label_i18n),
    )


async def update_option(db: asyncpg.Connection, option_id: int, data: dict) -> dict:
    """Update an existing option.

    Args:
        db: Active database connection.
        option_id: Primary key of the option.
        data: Dict with updated fields (code, label, color, sort_order, is_active).

    Returns:
        Updated option dict.

    Raises:
        ValueError: If the option is not found.
    """
    label_i18n = data.get("label_i18n") or {}
    result = await row(
        db,
        """UPDATE toolkit_field_options
           SET code=$1, label=$2, color=$3, sort_order=$4, is_active=$5, label_i18n=$7
           WHERE id=$6 RETURNING *""",
        data.get("code"), data.get("label"),
        data.get("color") or None,
        int(data.get("sort_order", 0)),
        bool(data.get("is_active", True)),
        option_id,
        json.dumps(label_i18n),
    )
    if not result:
        raise ValueError("Option not found")
    return result


async def delete_option(db: asyncpg.Connection, option_id: int) -> bool:
    """Delete an option.

    Args:
        db: Active database connection.
        option_id: Primary key of the option.

    Returns:
        True on success.
    """
    await execute(db, "DELETE FROM toolkit_field_options WHERE id=$1", option_id)
    return True


async def reorder_options(db: asyncpg.Connection, order: list) -> bool:
    """Update sort_order for a list of options.

    Args:
        db: Active database connection.
        order: List of dicts with 'id' and 'sort_order' keys.

    Returns:
        True on success.
    """
    for item in order:
        await execute(
            db,
            "UPDATE toolkit_field_options SET sort_order=$1 WHERE id=$2",
            int(item["sort_order"]), int(item["id"]),
        )
    return True
