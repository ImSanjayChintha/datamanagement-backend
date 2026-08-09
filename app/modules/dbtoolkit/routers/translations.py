"""
Module: toolkit.routers.translations
Purpose: Language list endpoint used by TranslationInput UI component.
         The old get/set translation endpoints that wrote to the `translations`
         table have been removed — multilingual data now lives directly in jsonb
         columns on each table.
"""
import logging

from fastapi import APIRouter, Depends
import asyncpg

from app.core.database import get_db, rows
from app.core.deps import get_current_admin
from app.core.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/translations", tags=["Toolkit - Translations"])


@router.post("/available-languages")
async def available_languages(
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Return the list of active languages for multilingual input tabs."""
    try:
        result = await rows(
            db,
            "SELECT code, is_default FROM pim.locales"
            " WHERE is_active = TRUE"
            " ORDER BY sort_order, code",
        )
        return ok(result)
    except Exception as e:
        logger.exception("available_languages failed")
        return err(str(e))
