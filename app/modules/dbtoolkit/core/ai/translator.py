"""
Module: toolkit.ai.translator
Purpose: Translate toolkit field labels and option values to multiple languages
         using Claude Haiku (fast, cost-effective for translation workloads).

The translate endpoint accepts a dict of field codes → source text and
returns a nested dict of field codes → { lang_code: translated_value }.
"""
import json
import logging
from app.core.config import settings
from app.modules.dbtoolkit.core.ai.client import call_claude, strip_json_fence
from app.modules.dbtoolkit.core.ai.prompts import load_config, build_translate_system
from app.modules.dbtoolkit.core.constants import AI_GENERATE_MAX_TOKENS

logger = logging.getLogger(__name__)


async def translate_texts(
    texts: dict[str, str],
    source_lang: str,
    target_langs: list[str],
) -> dict:
    """Translate a dict of field values into multiple target languages.

    Sends the texts to Claude Haiku with the translate system prompt and
    returns a nested translation dict.  The source language value is always
    preserved in the result even if Claude omits it.

    Args:
        texts: Dict of { field_code: source_text } to translate.
        source_lang: BCP-47 language code of the input texts (e.g. 'en').
        target_langs: List of target language codes (e.g. ['fr', 'de', 'es']).

    Returns:
        Nested dict of { field_code: { lang_code: translated_value } }.

    Raises:
        ValueError: If texts is empty, target_langs is empty, or the AI
            returns invalid JSON.
    """
    if not texts:
        raise ValueError("texts is required")
    if not target_langs:
        raise ValueError("target_langs must contain at least one language")

    cfg    = load_config()
    system = build_translate_system(cfg)
    user_msg = (
        f"Source language: {source_lang}\n"
        f"Target languages: {', '.join(target_langs)}\n\n"
        f"Texts to translate:\n{json.dumps(texts, ensure_ascii=False, indent=2)}"
    )

    raw = await call_claude(
        model=settings.CLAUDE_HAIKU,
        system=system,
        user_msg=user_msg,
        max_tokens=AI_GENERATE_MAX_TOKENS,
    )

    try:
        result = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI returned invalid JSON: {exc}. Raw: {raw[:300]}") from exc

    # Ensure source language value is always present in each field's translations
    for field_code, source_text in texts.items():
        if field_code in result:
            result[field_code].setdefault(source_lang, source_text)
        else:
            result[field_code] = {source_lang: source_text}

    return result
