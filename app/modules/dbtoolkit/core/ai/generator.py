"""
Module: toolkit.ai.generator
Purpose: Generate or regenerate toolkit objects (tables, views, functions)
         from natural-language prompts using Claude Sonnet.

Flow:
  1. Fetch schema context from the database (Option B — backend fetches internally).
  2. Build system prompt from prompts.json config.
  3. Call Claude and parse the JSON response.
  4. Return structured result for the router to send back to the frontend.
"""
import json
import logging
import asyncpg
from app.core.config import settings
from app.core.database import row
from app.modules.dbtoolkit.core.constants import AI_GENERATE_MAX_TOKENS, SYSTEM_FIELD_CODES
from app.modules.dbtoolkit.core.ai.client import call_claude, strip_json_fence
from app.modules.dbtoolkit.core.ai.prompts import load_config, build_generate_sql_system
from app.modules.dbtoolkit.core.ai.schema_context import fetch_schema_context, build_context_block

logger = logging.getLogger(__name__)


async def generate_sql(
    db: asyncpg.Connection,
    prompt: str,
    object_type: str | None = None,
    selected_tables: list[str] | None = None,
    clarify_answer: dict | None = None,
) -> dict:
    """Generate a table, view, or function definition from a natural-language prompt.

    Fetches schema context from the DB, builds a system prompt, calls Claude Sonnet,
    and returns a structured result.

    Args:
        db: Active database connection.
        prompt: Natural-language description of what to create.
        object_type: Optional hint ('table', 'view', 'function'). AI decides if None.
        selected_tables: Optional list of table codes to limit context scope.
        clarify_answer: Answer to a previous clarify response:
                        { clarify_type, selected: [...], specification: '...' }

    Returns:
        One of: table result dict, view result dict, function result dict,
        or clarify dict with { type: 'clarify', question, clarify_type, suggestions }.

    Raises:
        ValueError: If the AI returns invalid JSON.
    """
    cfg    = load_config()
    schema = await fetch_schema_context(db, selected_tables or None)

    context_label = "SELECTED TABLES / VIEWS TO USE" if selected_tables else None
    context_block = build_context_block(schema, label_override=context_label)

    type_hint = (
        f"\n\nThe user explicitly wants to create a {object_type.upper()}."
        if object_type else ""
    )

    clarify_block = ""
    if clarify_answer:
        ctype = clarify_answer.get("clarify_type", "")
        if ctype == "table_selection":
            selected = clarify_answer.get("selected") or []
            clarify_block = f"\n\nThe user confirmed these tables/views to use: {', '.join(selected)}."
        elif ctype == "specification":
            spec = clarify_answer.get("specification") or ""
            clarify_block = f"\n\nAdditional specification from user: {spec}"

    system = build_generate_sql_system(cfg) + context_block + type_hint + clarify_block
    raw    = await call_claude(
        model=settings.CLAUDE_SONNET,
        system=system,
        user_msg=prompt,
        max_tokens=AI_GENERATE_MAX_TOKENS,
    )

    try:
        result = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI returned invalid JSON: {exc}. Raw: {raw[:400]}") from exc

    if result.get("type") == "clarify":
        result.setdefault("allow_multiple", True)
        result.setdefault("suggestions", [])

    # Strip system fields (id, code, sort_order, audit cols) from AI-generated
    # table definitions — the backend always injects them from common_fields.
    if result.get("type") == "table" and isinstance(result.get("fields"), list):
        result["fields"] = [
            f for f in result["fields"]
            if (f.get("code") or "").strip().lower() not in SYSTEM_FIELD_CODES
        ]

    return result


async def regenerate_sql(
    db: asyncpg.Connection,
    object_id: int,
    extra_prompt: str = "",
) -> dict:
    """Regenerate the SQL for an existing view or function.

    Fetches the current SQL from toolkit_objects, sends it to Claude with
    the full schema context, and returns the updated SQL without executing it.

    Args:
        db: Active database connection.
        object_id: toolkit_objects.id of the object to regenerate.
        extra_prompt: Optional instruction describing what to change.

    Returns:
        Updated result dict with 'sql', 'object_id', 'object_code'.

    Raises:
        ValueError: If the object is not found or the AI response is invalid.
    """
    obj = await row(db, "SELECT * FROM toolkit_objects WHERE id=$1", object_id)
    if not obj:
        raise ValueError("Object not found")

    cfg    = load_config()
    schema = await fetch_schema_context(db)

    context_block = build_context_block(schema)
    modify_hint = (
        f"\n\nThe user wants to MODIFY the existing {obj['object_type'].upper()} "
        f"named '{obj['code']}'. Return the same JSON type with the updated SQL."
    )

    system = build_generate_sql_system(cfg) + context_block + modify_hint
    user_msg = (
        f"Rewrite the existing {obj['object_type']} '{obj['code']}'.\n\n"
        f"Current SQL:\n{obj['sql']}"
        + (f"\n\nChange instruction: {extra_prompt}" if extra_prompt else "")
    )

    raw = await call_claude(
        model=settings.CLAUDE_SONNET,
        system=system,
        user_msg=user_msg,
        max_tokens=AI_GENERATE_MAX_TOKENS,
    )

    try:
        result = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI returned invalid JSON: {exc}. Raw: {raw[:400]}") from exc

    if not result.get("sql"):
        raise ValueError("AI did not return updated SQL")

    return {**result, "object_id": obj["id"], "object_code": obj["code"]}
