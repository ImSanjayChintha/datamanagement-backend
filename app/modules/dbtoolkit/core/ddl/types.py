"""
Module: toolkit.ddl.types
Purpose: PostgreSQL type mapping and utility helpers for DDL generation.
         Centralises the field-type → pg-type conversion, SQL safe-naming,
         and multilingual field detection so every other DDL sub-module can
         import from one place.
"""
import re

# ---------------------------------------------------------------------------
# PostgreSQL type mapping
# ---------------------------------------------------------------------------

PG_TYPES: dict[str, str] = {
    "id":            "uuid",
    "text":          "text",
    "textarea":      "text",
    "richtext":      "text",
    "slug":          "text",
    "email":         "text",
    "phone":         "text",
    "url":           "text",
    "color":         "text",
    "file":          "text",
    "image":         "text",
    "integer":       "integer",
    "decimal":       "numeric",
    "currency":      "numeric(15,4)",
    "percentage":    "numeric(5,2)",
    "date":          "date",
    "datetime":      "timestamptz",
    "time":          "time",
    "toggle":        "boolean",
    "json":          "json",
    "jsonb":         "jsonb",
    "text_array":    "text[]",
    "inline_select": "text",
    "select":        "text",
    "sequence":      "bigint",
    "uuid":          "uuid",
    # Deprecated: 'translation' was the old multilingual type.
    # Any remaining DB rows with this field_type still resolve to jsonb so
    # that existing columns remain valid until the migration script is run.
    "translation":   "jsonb",
}
"""Mapping from toolkit field_type to PostgreSQL column type."""

# Re-exported for backward-compat with code that still imports _NO_COLUMN_TYPES
FIELD_TYPES_NO_COLUMN: frozenset[str] = frozenset({"multiselect", "computed"})
"""Field types that produce no physical column in the base table."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def pg_type(field: dict) -> str | None:
    """Return the PostgreSQL column type for a field, or None if no column.

    Returns None for virtual types (translation, multiselect, computed),
    the special 'daterange' type (which generates two columns handled separately),
    and any unrecognised types. Multilingual fields (is_multilingual=True) always
    return 'jsonb' so all languages are stored inline as {"en": "...", "es": "..."}.

    For 'decimal' fields, respects the 'precision' and 'scale' config keys.

    Args:
        field: Toolkit field dict with at least 'field_type' key.

    Returns:
        A PostgreSQL type string (e.g. 'text', 'numeric(10,4)') or None.
    """
    ftype = field["field_type"]
    if ftype in FIELD_TYPES_NO_COLUMN:
        return None
    if ftype == "daterange":
        return None  # two columns (_from / _to) handled by the caller
    if ftype == "decimal":
        cfg       = field.get("config") or {}
        precision = cfg.get("precision", 10)
        scale     = cfg.get("scale", 4)
        return f"numeric({precision},{scale})"
    # Multilingual fields store all translations inline as {"en": "...", "es": "..."}
    # regardless of the base field_type, the physical column must be jsonb.
    if field.get("is_multilingual"):
        return "jsonb"
    return PG_TYPES.get(ftype, "text")


def safe_name(name: str) -> str:
    """Convert an arbitrary string into a safe PostgreSQL identifier.

    Lowercases the string and replaces any character that is not a letter,
    digit, or underscore with an underscore.

    Args:
        name: Raw name string (e.g. 'My Table Code').

    Returns:
        Safe identifier string (e.g. 'my_table_code').
    """
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def is_field_multilingual(
    ref_fields_map: dict | None,
    ref_tbl: str,
    field_code: str,
) -> bool:
    """Return True if field_code in ref_tbl is marked multilingual.

    Looks up the field definition in ref_fields_map (a dict keyed by table
    code) and checks is_multilingual. Also accepts the deprecated field_type
    'translation' as a backward-compat fallback for rows not yet migrated.

    Args:
        ref_fields_map: Dict of {table_code: [field_dicts]}, may be None.
        ref_tbl: The table code to look up.
        field_code: The safe (snake_case) field code to search for.

    Returns:
        True if the field is multilingual, False otherwise (including when
        ref_fields_map is None or the table/field is not found).
    """
    if not ref_fields_map:
        return False
    fld = next(
        (f for f in (ref_fields_map.get(ref_tbl) or [])
         if safe_name(f.get("code", "")) == field_code),
        None,
    )
    # Also treat old 'translation' type rows as multilingual (backward compat)
    return fld is not None and bool(
        fld.get("is_multilingual") or fld.get("field_type") == "translation"
    )
