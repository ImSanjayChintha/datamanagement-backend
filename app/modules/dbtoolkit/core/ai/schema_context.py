"""
Module: toolkit.ai.schema_context
Purpose: Fetch the current database schema from toolkit_tables and
         toolkit_objects and format it as a compact text block suitable
         for inclusion in an AI system prompt.
"""
import json
import logging
import asyncpg
from app.core.database import rows

logger = logging.getLogger(__name__)


async def fetch_schema_context(
    db: asyncpg.Connection,
    selected_tables: list[str] | None = None,
) -> dict:
    """Fetch schema metadata from the database for AI context.

    Reads toolkit_tables.metadata_json (tables) and toolkit_objects.metadata_json
    (existing views/functions).  When selected_tables is given, only those
    table codes are included.

    Args:
        db: Active database connection.
        selected_tables: Optional list of table codes to limit context scope.

    Returns:
        Dict with keys 'tables' and 'objects', each a list of metadata dicts.
    """
    if selected_tables:
        tbl_rows = await rows(
            db,
            "SELECT code, metadata_json FROM toolkit_tables "
            "WHERE is_active=true AND metadata_json IS NOT NULL AND code=ANY($1::text[]) "
            "ORDER BY code",
            selected_tables,
        )
    else:
        tbl_rows = await rows(
            db,
            "SELECT code, metadata_json FROM toolkit_tables "
            "WHERE is_active=true AND metadata_json IS NOT NULL ORDER BY code",
        )

    obj_rows = await rows(
        db,
        "SELECT code, object_type, metadata_json FROM toolkit_objects "
        "WHERE is_active=true AND metadata_json IS NOT NULL ORDER BY object_type, code",
    )

    def _parse(r: dict, kind_key: str = "object_type", kind_default: str | None = None) -> dict | None:
        meta = r["metadata_json"]
        if not meta:
            return None
        entry = meta if isinstance(meta, dict) else json.loads(meta)
        entry["_kind"] = r.get(kind_key) or kind_default
        entry.setdefault("code", r["code"])
        return entry

    tables  = [e for r in tbl_rows if (e := _parse(r, kind_default="table")) is not None]
    objects = [e for r in obj_rows if (e := _parse(r))                        is not None]
    return {"tables": tables, "objects": objects}


def build_context_block(schema: dict, label_override: str | None = None) -> str:
    """Convert a schema context dict into a compact text block for Claude.

    Args:
        schema: Dict returned by fetch_schema_context().
        label_override: Optional heading override for the tables section.

    Returns:
        Multi-line string describing all tables and objects in the schema.
    """
    tables  = schema.get("tables") or []
    objects = schema.get("objects") or []
    lines: list[str] = []

    if tables:
        heading = label_override or "EXISTING TABLES — available for JOINs and references"
        lines.append(f"\n\n{heading}:")
        for t in tables:
            code  = t.get("code", "?")
            label = t.get("label", code)
            desc  = (t.get("description") or "").strip()
            lines.append(f"\n• {code} ({label})" + (f": {desc}" if desc else ""))
            for f in t.get("fields", []):
                ft   = f.get("field_type", "text")
                req  = " [required]" if f.get("is_required") else ""
                uniq = " [unique]"   if f.get("is_unique")   else ""
                ref  = f" → refs {f['ref_table_code']}" if f.get("ref_table_code") else ""
                opts = ""
                if f.get("options"):
                    opts = " options=[" + ", ".join(o.get("code", "") for o in f["options"]) + "]"
                dv = f" default={f['default_value']}" if f.get("default_value") is not None else ""
                lines.append(f"    - {f['code']} ({ft}){req}{uniq}{ref}{opts}{dv}")

    if objects:
        lines.append("\n\nEXISTING VIEWS / FUNCTIONS — do not recreate these:")
        for o in objects:
            kind    = o.get("_kind", "object")
            code    = o.get("code", "?")
            sig     = o.get("signature", "")
            cols    = o.get("columns") or []
            col_str = ", ".join(cols) if cols else ""
            meta_str = f"({sig})" if sig else (f"→ columns: {col_str}" if col_str else "")
            lines.append(f"\n• [{kind}] {code} {meta_str}".rstrip())

    return "\n".join(lines)
