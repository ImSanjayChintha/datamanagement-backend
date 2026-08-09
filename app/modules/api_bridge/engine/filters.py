"""Filter DSL compiler.

Compiles a filter node tree into a parameterized SQL fragment.
Column identifiers are ALWAYS taken from the registry (FieldMeta.field_name),
never from the user-supplied request body. Values are ALWAYS bound via $N
parameters — no string concatenation of user data into SQL.
"""
from __future__ import annotations

from typing import Any

from .errors import EngineError, unknown_field
from .registry import I18N_TYPES

MAX_NESTING_DEPTH = 5
MAX_LEAF_COUNT    = 50

TEXT_OPS   = frozenset({"ilike"})
JSON_OPS   = frozenset({"contains"})
ARRAY_OPS  = frozenset({"in", "nin"})
RANGE_OPS  = frozenset({"between"})
NULL_OPS   = frozenset({"is_null"})
SCALAR_OPS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte"})
ALL_OPS    = SCALAR_OPS | TEXT_OPS | JSON_OPS | ARRAY_OPS | RANGE_OPS | NULL_OPS


class ParamBag:
    """Accumulates bound parameters; generates $N placeholders.

    Shared across filter, sort, and pagination compilation so all $N indices
    within a single query are globally sequential.
    """

    def __init__(self) -> None:
        self._values: list[Any] = []

    def add(self, value: Any) -> str:
        """Append value and return its $N placeholder."""
        self._values.append(value)
        return f"${len(self._values)}"

    @property
    def values(self) -> tuple:
        return tuple(self._values)

    def __len__(self) -> int:
        return len(self._values)


def compile_filter(
    node: dict,
    fields_by_name: dict,
    params: ParamBag,
    *,
    locale: str | None = None,
    _depth: int = 0,
    _leaf_count: list[int] | None = None,
) -> str:
    """Recursively compile a filter DSL node into a SQL WHERE fragment.

    Returns a non-empty string (always parenthesised for composability).
    Raises EngineError on unknown fields, unknown operators, or constraint violations.
    """
    if _leaf_count is None:
        _leaf_count = [0]

    if _depth > MAX_NESTING_DEPTH:
        raise EngineError(
            "validation_failed",
            f"Filter nesting depth exceeds maximum of {MAX_NESTING_DEPTH}",
        )

    if "and" in node:
        children = node["and"]
        if not children:
            return "TRUE"
        parts = [
            compile_filter(sub, fields_by_name, params, locale=locale,
                           _depth=_depth + 1, _leaf_count=_leaf_count)
            for sub in children
        ]
        return f"({' AND '.join(parts)})"

    if "or" in node:
        children = node["or"]
        if not children:
            return "FALSE"
        parts = [
            compile_filter(sub, fields_by_name, params, locale=locale,
                           _depth=_depth + 1, _leaf_count=_leaf_count)
            for sub in children
        ]
        return f"({' OR '.join(parts)})"

    # Leaf node
    _leaf_count[0] += 1
    if _leaf_count[0] > MAX_LEAF_COUNT:
        raise EngineError("validation_failed", f"Filter exceeds {MAX_LEAF_COUNT} conditions")

    return _compile_leaf(node, fields_by_name, params, locale)


def _compile_leaf(
    node:           dict,
    fields_by_name: dict,
    params:         ParamBag,
    locale:         str | None,
) -> str:
    field_name  = node.get("field")
    op          = node.get("op")
    value       = node.get("value")
    leaf_locale = node.get("locale", locale)

    # Whitelist: field must exist in the registered object's field list
    if not field_name or field_name not in fields_by_name:
        raise unknown_field(field_name or "(missing field)")

    # Whitelist: operator must be one of the known enum values
    if op not in ALL_OPS:
        raise EngineError("validation_failed", f"Unknown filter operator: {op!r}")

    field = fields_by_name[field_name]

    # Column reference — identifier always sourced from registry, never from user input.
    # All queries alias the main table as 't' for unambiguous column qualification.
    col = _col_ref(field, leaf_locale)

    # ── NULL check ─────────────────────────────────────────────────────────────
    if op in NULL_OPS:
        negate = value is False
        return f"{col} IS {'NOT ' if negate else ''}NULL"

    # ── BETWEEN ────────────────────────────────────────────────────────────────
    if op in RANGE_OPS:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise EngineError("validation_failed", "'between' requires value=[a, b]")
        p1 = params.add(value[0])
        p2 = params.add(value[1])
        return f"{col} BETWEEN {p1} AND {p2}"

    # ── Operator/type compatibility ────────────────────────────────────────────
    if op in TEXT_OPS and field.field_type not in ("text", "i18n_text", "i18n_richtext", "select"):
        raise EngineError("validation_failed", f"Operator '{op}' is only valid on text fields")

    if op in JSON_OPS and field.field_type not in ("json", "i18n_text", "i18n_richtext"):
        raise EngineError("validation_failed", f"Operator '{op}' is only valid on JSON fields")

    # ── Normal scalar / array operators ───────────────────────────────────────
    p = params.add(value)

    return {
        "eq":       f"{col} = {p}",
        "neq":      f"{col} <> {p}",
        "gt":       f"{col} > {p}",
        "gte":      f"{col} >= {p}",
        "lt":       f"{col} < {p}",
        "lte":      f"{col} <= {p}",
        "in":       f"{col} = ANY({p})",
        "nin":      f"{col} <> ALL({p})",
        "ilike":    f"{col} ILIKE {p}",
        "contains": f"{col} @> {p}::jsonb",
    }[op]


def _col_ref(field, locale: str | None) -> str:
    """Safe column reference. Identifier from registry; locale literal escaped."""
    qualified = f't."{field.field_name}"'
    if field.field_type in I18N_TYPES and locale:
        safe_locale = locale.replace("'", "")
        return f"pim.tr({qualified}, '{safe_locale}')"
    return qualified
