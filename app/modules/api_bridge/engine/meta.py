"""Builds the /api/meta response.

/api/meta returns either a list of all registered objects or a full field
descriptor for a single object, including locale-aware annotations for i18n
fields and action availability filtered by the caller's roles.
"""
from __future__ import annotations

import asyncpg

from .errors import EngineError, unknown_object
from .registry import I18N_TYPES, ObjectMeta, registry


async def build_meta(
    body:       dict,
    db:         asyncpg.Connection,
    user_roles: list[str],
) -> dict:
    if body.get("list"):
        return await _list_objects(db, user_roles)

    obj_code = body.get("object")
    if not obj_code:
        raise EngineError("validation_failed", "Provide 'object' (code) or 'list: true'")

    obj = await registry.get(obj_code, db)
    if not obj:
        raise unknown_object(obj_code)

    return await _describe_object(obj, db, user_roles)


async def _list_objects(db: asyncpg.Connection, user_roles: list[str]) -> dict:
    codes = await registry.list_codes(db)
    return {"objects": codes}


async def _describe_object(
    obj:        ObjectMeta,
    db:         asyncpg.Connection,
    user_roles: list[str],
) -> dict:
    # Active locales — used to annotate i18n field descriptors
    locale_rows = await db.fetch(
        "SELECT code FROM pim.locales WHERE is_active = TRUE ORDER BY sort_order, code"
    )
    active_locales = [r["code"] for r in locale_rows]

    available_actions = [
        action
        for action in obj.actions
        if _role_allowed(user_roles, obj.allowed_roles(action))
    ]

    hidden = obj.hidden_fields()

    field_descs = []
    for f in obj.fields:
        if f.field_name in hidden:
            continue
        desc: dict = {
            "name":               f.field_name,
            "type":               f.field_type,
            "label":              f.name,
            "required":           f.is_required,
            "unique":             f.is_unique,
            "readonly":           f.is_readonly,
            "editable_on_update": f.editable_on_update,
            "in_list":            f.in_list,
            "in_form":            f.in_form,
        }
        if f.description:
            desc["description"] = f.description
        if f.default_value is not None:
            desc["default"] = f.default_value
        if f.field_type in I18N_TYPES:
            desc["locales"] = active_locales
        if f.ref:
            desc["ref"] = f.ref
        if f.options:
            desc["options"] = f.options
        if f.validation:
            desc["validation"] = f.validation
        field_descs.append(desc)

    default_cols = [
        f.field_name
        for f in obj.fields
        if f.in_list and f.field_name not in hidden
    ]
    sort_field = next(
        (f.field_name for f in obj.fields if f.field_name == "sort_order"),
        next((f.field_name for f in obj.fields if f.field_name == "code"), obj.pk_field),
    )

    return {
        "object":      obj.code,
        "kind":        obj.kind,
        "name":        obj.name,
        "pk_field":    obj.pk_field,
        "label_field": obj.label_field,
        "actions":     available_actions,
        "fields":      field_descs,
        "list_defaults": {
            "columns": default_cols,
            "sort":    [{"field": sort_field, "dir": "asc"}],
            "limit":   obj.default_limit(),
        },
    }


def _role_allowed(user_roles: list[str], allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return bool(set(user_roles) & set(allowed))
