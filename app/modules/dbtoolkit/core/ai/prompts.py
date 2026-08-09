"""
Module: toolkit.ai.prompts
Purpose: Load the AI prompt configuration from ai_config/prompts.json and
         assemble system prompt strings for each Claude endpoint.

The JSON config is loaded once per process and cached via @lru_cache.
Restart the server to pick up changes to prompts.json.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config" / "prompts.json"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load and cache the AI prompt configuration.

    Returns:
        Parsed contents of ai_config/prompts.json.

    Raises:
        FileNotFoundError: If prompts.json does not exist.
    """
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_generate_sql_system(cfg: dict) -> str:
    """Assemble the system prompt for the generate-sql endpoint.

    Args:
        cfg: Loaded prompts config dict (from load_config()).

    Returns:
        Fully assembled system prompt string.
    """
    gen         = cfg["generate_sql"]
    field_types = ", ".join(cfg["field_types"])
    std_cols    = ", ".join(cfg["standard_columns"])
    rules_text  = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(gen["rules"]))

    formats = []
    for name, fmt in gen["output_formats"].items():
        if name.startswith("_"):
            continue
        desc  = fmt.get("_description", "")
        clean = {k: v for k, v in fmt.items() if not k.startswith("_")}
        formats.append(
            f"### {name.upper()}{' — ' + desc if desc else ''}\n"
            + json.dumps(clean, indent=2)
        )

    clarify_notes = "\n".join(
        f"  {k}: {v}" for k, v in gen.get("clarify_types", {}).items()
    )

    return (
        f"{gen['role']}\n\n"
        f"AVAILABLE FIELD TYPES (tables only): {field_types}\n\n"
        f"AUTO-ADDED COLUMNS — always present, never include in table output:\n{std_cols}\n\n"
        f"NOTE: is_active is NOT auto-added — include it when clearly relevant.\n"
        f"CRITICAL: 'label' is a RESERVED system field code — NEVER generate a user field with code='label'.\n"
        f"If the user asks for a 'name', 'title', or similar field, use THEIR exact name as the snake_case code.\n\n"
        f"RULES:\n{rules_text}\n\n"
        f"CLARIFY TYPES:\n{clarify_notes}\n\n"
        f"OUTPUT FORMATS — respond with exactly one of these JSON shapes:\n\n"
        + "\n\n".join(formats)
    )


def build_translate_system(cfg: dict) -> str:
    """Assemble the system prompt for the translate endpoint.

    Args:
        cfg: Loaded prompts config dict (from load_config()).

    Returns:
        System prompt string for the translation Claude call.
    """
    tr    = cfg["translate"]
    rules = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(tr["rules"]))
    return f"{tr['role']}\n\nRULES:\n{rules}"


def build_suggest_system(cfg: dict) -> str:
    """Assemble the system prompt for the suggest endpoint.

    Args:
        cfg: Loaded prompts config dict (from load_config()).

    Returns:
        System prompt string for the suggest Claude call.
    """
    sg          = cfg["suggest"]
    rules       = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(sg["rules"]))
    field_types = ", ".join(cfg["field_types"])
    return f"{sg['role']}\n\nRULES:\n{rules}\n\nAVAILABLE FIELD TYPES: {field_types}"
