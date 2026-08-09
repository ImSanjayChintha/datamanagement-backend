"""Field-level validation for insert and update payloads.

All errors are accumulated before raising so the caller receives the full
list of problems in a single response, not just the first one found.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import validation_failed
from .registry import ObjectMeta, FieldMeta, I18N_TYPES


def validate_insert_data(
    obj:            ObjectMeta,
    data:           dict,
    default_locale: str = "en",
) -> dict:
    """Validate and coerce insert payload.

    Returns a cleaned dict (readonly fields stripped, values type-checked).
    Raises EngineError("validation_failed", …) with per-field error details.
    """
    errors: dict = {}

    # Pass 1: reject unknown / forbidden keys
    for key in data:
        if key.endswith("~merge"):
            errors[key] = {
                "code":    "invalid_key",
                "message": "~merge suffix is only valid on update",
            }
            continue
        f = obj.fields_by_name.get(key)
        if f is None:
            errors[key] = {"code": "unknown_field", "message": f"Unknown field: {key!r}"}
        elif f.is_readonly:
            errors[key] = {"code": "readonly", "message": f"'{key}' is system-managed"}

    if errors:
        raise validation_failed(errors)

    cleaned: dict = {}

    # Pass 2: required checks + type validation per registered field
    for f in obj.fields:
        if f.is_readonly:
            continue

        value = data.get(f.field_name)

        if value is None:
            if f.is_required:
                errors[f.field_name] = {
                    "code":    "required",
                    "message": (
                        f"'{f.field_name}' requires at least locale key '{default_locale}'"
                        if f.field_type in I18N_TYPES
                        else f"'{f.field_name}' is required"
                    ),
                }
            continue  # optional with no value — DB default will apply

        value = _coerce_value(f, value)
        err = _validate_value(f, value, default_locale)
        if err:
            errors[f.field_name] = err
        else:
            cleaned[f.field_name] = value

    if errors:
        raise validation_failed(errors)

    return cleaned


def validate_update_data(
    obj:            ObjectMeta,
    data:           dict,
    default_locale: str = "en",
) -> dict:
    """Validate partial update payload.

    Accepts field_name~merge for JSONB merge semantics on i18n fields.
    Returns a cleaned dict preserving the ~merge suffix.
    Raises EngineError("validation_failed", …) with per-field error details.
    """
    errors:  dict = {}
    cleaned: dict = {}

    for key, value in data.items():
        is_merge   = key.endswith("~merge")
        field_name = key[:-6] if is_merge else key

        f = obj.fields_by_name.get(field_name)
        if f is None:
            errors[key] = {"code": "unknown_field", "message": f"Unknown field: {key!r}"}
            continue

        if f.is_readonly:
            errors[field_name] = {
                "code":    "readonly",
                "message": f"'{field_name}' is system-managed",
            }
            continue

        if not f.editable_on_update:
            errors[field_name] = {
                "code":    "not_editable_on_update",
                "message": f"'{field_name}' cannot be changed after creation",
            }
            continue

        if value is None:
            cleaned[key] = value  # explicit null — allowed for optional fields
            continue

        value = _coerce_value(f, value)
        err = _validate_value(f, value, default_locale)
        if err:
            errors[field_name] = err
        else:
            cleaned[key] = value  # preserve ~merge suffix

    if errors:
        raise validation_failed(errors)

    return cleaned


def _coerce_value(field: FieldMeta, value: Any) -> Any:
    """Lightly coerce common JSON-deserialization mismatches before validation.

    Handles the most frequent cases so callers don't need to pre-process:
    - boolean fields receiving "true"/"false" strings or 0/1 integers
    - number/decimal fields receiving digit strings
    """
    ft = field.field_type
    if ft == "boolean" and not isinstance(value, bool):
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true",  "1", "yes"): return True
            if low in ("false", "0", "no"):  return False
        if isinstance(value, int):
            return bool(value)
    if ft in ("number", "decimal") and isinstance(value, str):
        stripped = value.strip()
        try:
            return int(stripped) if stripped.lstrip("-").isdigit() else float(stripped)
        except ValueError:
            pass  # fall through — _validate_value will report the type error
    return value


def _validate_value(field: FieldMeta, value: Any, default_locale: str) -> dict | None:
    """Return an error dict if value is invalid for this field type, else None."""
    ft = field.field_type

    if ft in I18N_TYPES:
        if not isinstance(value, dict):
            return {"code": "type_error", "message": "Expected a JSON object with locale keys"}
        if not value:
            return {"code": "type_error", "message": "i18n object must not be empty"}
        return None

    if ft in ("text", "select"):
        if not isinstance(value, str):
            return {"code": "type_error", "message": "Expected a string"}
    elif ft in ("number", "decimal"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return {"code": "type_error", "message": "Expected a number"}
    elif ft == "boolean":
        if not isinstance(value, bool):
            return {"code": "type_error", "message": "Expected true or false"}
    elif ft == "json":
        if not isinstance(value, (dict, list)):
            return {"code": "type_error", "message": "Expected a JSON object or array"}

    rules = field.validation or {}

    if "max_length" in rules and isinstance(value, str):
        if len(value) > int(rules["max_length"]):
            return {"code": "max_length", "message": f"Exceeds max length of {rules['max_length']}"}

    if "min" in rules and isinstance(value, (int, float)):
        if value < rules["min"]:
            return {"code": "min_value", "message": f"Must be ≥ {rules['min']}"}

    if "max" in rules and isinstance(value, (int, float)):
        if value > rules["max"]:
            return {"code": "max_value", "message": f"Must be ≤ {rules['max']}"}

    if "regex" in rules and isinstance(value, str):
        if not re.fullmatch(rules["regex"], value):
            return {"code": "regex", "message": "Does not match required pattern"}

    if ft == "select" and field.options:
        valid_values = {
            str(o.get("value", o)) if isinstance(o, dict) else str(o)
            for o in field.options
        }
        if str(value) not in valid_values:
            return {"code": "invalid_option", "message": f"Value {value!r} is not a valid option"}

    return None
