"""
Module: toolkit.ai.suggester
Purpose: Suggest improvements to an existing toolkit table definition using
         Claude Haiku.  Returns actionable suggestions, missing field
         recommendations, and design warnings.
"""
import json
import logging
import asyncpg
from app.core.config import settings
from app.modules.dbtoolkit.core.ai.client import call_claude, strip_json_fence
from app.modules.dbtoolkit.core.ai.prompts import load_config, build_suggest_system
from app.modules.dbtoolkit.core.ai.schema_context import fetch_schema_context, build_context_block
from app.modules.dbtoolkit.core.constants import AI_DEFAULT_MAX_TOKENS

logger = logging.getLogger(__name__)


async def suggest_improvements(
    db: asyncpg.Connection,
    definition: dict,
) -> dict:
    """Review a table definition and return structured improvement suggestions.

    Fetches the full schema context so Claude can reference existing tables
    when suggesting cross-table improvements or references.

    Args:
        db: Active database connection.
        definition: Table definition dict in the same format as the generate-sql
            table output (code, label, fields, etc.).

    Returns:
        Dict with keys:
          - suggestions: List of actionable improvement strings.
          - missing_fields: List of complete field dicts ready to add.
          - warnings: List of potential data integrity or design issue strings.

    Raises:
        ValueError: If definition is empty or the AI returns invalid JSON.
    """
    if not definition:
        raise ValueError("definition is required")

    cfg    = load_config()
    schema = await fetch_schema_context(db)

    context_block = build_context_block(schema)
    system        = build_suggest_system(cfg) + context_block

    raw = await call_claude(
        model=settings.CLAUDE_HAIKU,
        system=system,
        user_msg=f"Review this table definition:\n{json.dumps(definition, indent=2)}",
        max_tokens=AI_DEFAULT_MAX_TOKENS,
    )

    try:
        result = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        # Graceful fallback — return raw text as a single suggestion
        result = {"suggestions": [raw], "missing_fields": [], "warnings": []}

    return result
