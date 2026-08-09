"""
Module: toolkit.routers.ai
Purpose: Thin HTTP handlers for AI-assisted generation, translation, and suggestion.
         All business logic is delegated to the ai sub-package modules.
"""
import json
import logging

from fastapi import APIRouter, Body, Depends
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import ok, err
from app.modules.dbtoolkit.core.ai import generator, translator, suggester

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/ai", tags=["Toolkit - AI"])


@router.post("/generate-sql")
async def generate_sql_endpoint(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Generate a table, view, or function from a natural-language prompt."""
    try:
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return err("prompt is required")
        result = await generator.generate_sql(
            db=db,
            prompt=prompt,
            object_type=body.get("object_type"),
            selected_tables=body.get("selected_tables") or [],
            clarify_answer=body.get("clarify_answer") or {},
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("generate_sql failed")
        return err(str(e))


@router.post("/regenerate-sql")
async def regenerate_sql_endpoint(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Re-generate an existing view or function with optional change instructions."""
    try:
        oid   = body.get("id")
        extra = (body.get("prompt") or "").strip()
        if not oid:
            return err("id is required")
        result = await generator.regenerate_sql(db=db, object_id=oid, extra_prompt=extra)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("regenerate_sql failed")
        return err(str(e))


@router.post("/generate-table")
async def generate_table(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Deprecated. Use /generate-sql with object_type='table'."""
    body["object_type"] = "table"
    return await generate_sql_endpoint(body=body, db=db, admin=admin)


@router.post("/translate")
async def translate_text(
    body: dict = Body(default={}),
    admin: dict = Depends(get_current_admin),
):
    """Translate field values into multiple target languages."""
    try:
        texts        = body.get("texts") or {}
        source_lang  = (body.get("source_lang") or "en").strip()
        target_langs = [l.strip() for l in (body.get("target_langs") or []) if l.strip()]

        result = await translator.translate_texts(
            texts=texts,
            source_lang=source_lang,
            target_langs=target_langs,
        )
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("translate_text failed")
        return err(str(e))


@router.post("/suggest")
async def suggest_improvements_endpoint(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Suggest improvements to a table definition."""
    try:
        definition = body.get("definition")
        if not definition:
            return err("definition is required")
        result = await suggester.suggest_improvements(db=db, definition=definition)
        return ok(result)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("suggest_improvements failed")
        return err(str(e))
