"""
Module: toolkit.constants
Purpose: All toolkit-specific constants — field type catalogs, SQL naming
         conventions, default values, and configuration that must not be
         scattered across implementation files.
"""

# ---------------------------------------------------------------------------
# Field type catalog
# ---------------------------------------------------------------------------

FIELD_TYPES_ALL: frozenset[str] = frozenset({
    "id", "text", "textarea", "richtext", "slug", "email", "phone", "url",
    "color", "file", "image", "integer", "decimal", "currency", "percentage",
    "date", "datetime", "time", "daterange", "toggle", "json", "jsonb",
    "inline_select", "select", "multiselect",
    "sequence", "uuid", "computed",
    # "translation" is deprecated — existing DB rows with this type are treated as
    # field_type="json" + is_multilingual=True.  New fields must use json+is_multilingual.
})
"""All field types recognised by the toolkit."""

FIELD_TYPES_NO_COLUMN: frozenset[str] = frozenset({"multiselect", "computed"})
"""Field types that produce no physical column in the base table."""

FIELD_TYPES_NO_DEFAULT: frozenset[str] = frozenset({
    "id", "sequence", "uuid", "computed", "multiselect", "daterange", "file", "image",
})
"""Field types for which a default_value makes no sense in the UI."""

# ---------------------------------------------------------------------------
# SQL naming conventions  (centralised so renaming is a one-liner)
# ---------------------------------------------------------------------------

VIEW_PREFIX = "v_"
"""Prefix applied to every auto-generated view name."""

LIST_FN_PREFIX = "fn_list_"
"""Prefix applied to every auto-generated list function."""

GET_FN_PREFIX = "fn_get_"
"""Prefix applied to every auto-generated get function."""

JUNCTION_TABLE_SUFFIX = "_links"
"""Suffix appended to junction tables for multiselect fields:  {table}_{field}_links."""

AUDIT_TRIGGER_FN = "fn_toolkit_set_audit"
"""PostgreSQL function invoked by the audit trigger on every toolkit table."""

# ---------------------------------------------------------------------------
# Column / field codes
# ---------------------------------------------------------------------------

AUDIT_FIELD_CODES: frozenset[str] = frozenset({
    "inserted_at", "inserted_by", "modified_at", "modified_by",
})
"""Audit columns automatically populated by the audit trigger."""

SYSTEM_FIELD_CODES: frozenset[str] = frozenset({
    "id", "code", "sort_order",
    "is_active",
    "inserted_at", "inserted_by", "modified_at", "modified_by",
})
"""All auto-added system columns; AI is instructed to skip these."""

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

DEFAULT_SCHEMA = "toolkit"
DEFAULT_STORE_FIELD = "code"
"""Default foreign-key store field when none is specified."""

DEFAULT_DISPLAY_FIELD = "label"
"""Default display field for reference dropdowns."""

DEFAULT_ORDER_BY = "sort_order, code"
DEFAULT_SORT_ORDER = 0
DEFAULT_LANG = "en"
"""Fallback language code used in translation COALESCE expressions."""

LANG_SESSION_VAR = "app.lang"
"""PostgreSQL session variable that carries the current request language."""

USER_SESSION_VAR = "app.current_user"
"""PostgreSQL session variable used by the audit trigger."""

# ---------------------------------------------------------------------------
# SQL Console
# ---------------------------------------------------------------------------

SQL_MAX_ROWS = 500
"""Maximum rows returned by a SELECT in the SQL Console."""

DEFAULT_QUERY_LIMIT = 50
"""Default page size for list endpoints."""

SQL_BLOCKED_PATTERNS: frozenset[str] = frozenset({
    "drop table admin_users",
    "drop table toolkit_tables",
    "drop table toolkit_fields",
    "truncate admin_users",
})
"""Case-insensitive SQL fragments that are always rejected by the console."""

# ---------------------------------------------------------------------------
# AI / Anthropic
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_TIMEOUT_SECONDS = 60
AI_GENERATE_MAX_TOKENS = 4096
AI_DEFAULT_MAX_TOKENS = 2048

# ---------------------------------------------------------------------------
# Toolkit object types
# ---------------------------------------------------------------------------

OBJECT_TYPES: frozenset[str] = frozenset({"view", "function", "trigger"})
"""Valid values for toolkit_objects.object_type."""
