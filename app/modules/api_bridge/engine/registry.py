from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Field types that store multilingual JSONB values
I18N_TYPES = frozenset({"i18n_text", "i18n_richtext"})


@dataclass
class FieldMeta:
    id:                 int
    code:               str
    object_code:        str
    field_name:         str
    field_type:         str
    name:               dict
    description:        dict | None
    is_required:        bool
    is_unique:          bool
    is_readonly:        bool
    editable_on_update: bool
    default_value:      Any
    ref:                dict | None
    options:            list | None
    validation:         dict | None
    in_list:            bool
    in_form:            bool
    sort_order:         int


@dataclass
class ObjectMeta:
    id:            int
    code:          str        # "{schema}.{object_name}"
    api_slug:      str | None # public URL slug (defaults to object_name)
    resource_code: str | None # linked api_gateway.api_resources.code for auth
    schema_name:   str
    object_name:  str
    kind:         str       # table | view | function
    name:         dict      # {"en": "Countries", "es": "Países"}
    label_field:  str | None
    pk_field:     str
    actions:      dict      # {"list": {"impl": "generic"}, ...}
    policy:       dict      # {"roles": {"list": ["*"], "delete": ["admin"]}, ...}
    fields:       list[FieldMeta]

    # Derived index — built in __post_init__
    fields_by_name: dict[str, FieldMeta] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.fields_by_name = {f.field_name: f for f in self.fields}

    def action_def(self, action: str) -> dict | None:
        return self.actions.get(action)

    def allowed_roles(self, action: str) -> list[str]:
        return (self.policy or {}).get("roles", {}).get(action, ["*"])

    def max_limit(self) -> int:
        return int((self.policy or {}).get("max_limit", 200))

    def default_limit(self) -> int:
        return int((self.policy or {}).get("default_limit", 25))

    def max_affected(self) -> int:
        return int((self.policy or {}).get("max_affected", 500))

    def hidden_fields(self) -> set[str]:
        return set((self.policy or {}).get("hidden_fields", []))

    def is_view(self) -> bool:
        return self.kind == "view"

    def soft_delete(self) -> bool:
        return bool((self.policy or {}).get("soft_delete", False))


class RegistryCache:
    """In-process per-object registry cache with async write lock.

    Cache keys are either canonical codes ("pim.countries") or the alias key
    used in the URL ("gateway.countries"). Both point to the same ObjectMeta
    instance so a slug-based lookup is only one dict read after the first hit.
    """

    def __init__(self) -> None:
        self._cache: dict[str, ObjectMeta] = {}
        self._lock  = asyncio.Lock()

    async def get(self, code: str, db: asyncpg.Connection) -> ObjectMeta | None:
        if code in self._cache:
            return self._cache[code]
        async with self._lock:
            if code in self._cache:
                return self._cache[code]
            # 1. Exact code match (schema explicit in URL, e.g. "pim.countries")
            obj = await self._load(code, db)
            # 2. Slug fallback: URL used a virtual namespace like "gateway.countries"
            if obj is None and "." in code:
                slug = code.split(".", 1)[1]
                obj  = await self._load_by_slug(slug, db)
            if obj is not None:
                # Cache under both the requested key and the canonical code
                self._cache[code]      = obj
                self._cache[obj.code]  = obj
            return obj

    async def invalidate(self, code: str | None = None) -> None:
        async with self._lock:
            if code:
                # Remove canonical key and every alias that points to the same object
                stale = [k for k, v in self._cache.items() if v.code == code or k == code]
                for k in stale:
                    self._cache.pop(k, None)
            else:
                self._cache.clear()
        logger.info("engine registry: invalidated %s", code or "all")

    async def list_codes(self, db: asyncpg.Connection) -> list[dict]:
        rows = await db.fetch(
            """
            SELECT code, api_slug, resource_code, schema_name, object_name, name, kind, sort_order
            FROM   toolkit.api_objects
            WHERE  is_active = TRUE
            ORDER  BY sort_order, code
            """
        )
        return [dict(r) for r in rows]

    async def _load_by_slug(self, slug: str, db: asyncpg.Connection) -> ObjectMeta | None:
        obj_row = await db.fetchrow(
            """
            SELECT id, code, api_slug, resource_code, schema_name, object_name, kind,
                   "name", label_field, pk_field, actions, policy
            FROM   toolkit.api_objects
            WHERE  api_slug = $1 AND is_active = TRUE
            """,
            slug,
        )
        if not obj_row:
            return None
        return await self._load_fields(obj_row, db)

    async def _load(self, code: str, db: asyncpg.Connection) -> ObjectMeta | None:
        obj_row = await db.fetchrow(
            """
            SELECT id, code, api_slug, resource_code, schema_name, object_name, kind,
                   "name", label_field, pk_field, actions, policy
            FROM   toolkit.api_objects
            WHERE  code = $1 AND is_active = TRUE
            """,
            code,
        )
        if not obj_row:
            return None
        return await self._load_fields(obj_row, db)

    async def _load_fields(self, obj_row: asyncpg.Record, db: asyncpg.Connection) -> ObjectMeta:
        field_rows = await db.fetch(
            """
            SELECT id, code, object_code, field_name, field_type,
                   "name", description, is_required, is_unique, is_readonly,
                   editable_on_update, default_value, ref, options, validation,
                   in_list, in_form, sort_order
            FROM   toolkit.api_fields
            WHERE  object_code = $1 AND is_active = TRUE
            ORDER  BY sort_order, field_name
            """,
            obj_row["code"],
        )

        def _to_field(r: asyncpg.Record) -> FieldMeta:
            return FieldMeta(
                id                 = r["id"],
                code               = r["code"],
                object_code        = r["object_code"],
                field_name         = r["field_name"],
                field_type         = r["field_type"],
                name               = r["name"] or {},
                description        = r["description"],
                is_required        = bool(r["is_required"]),
                is_unique          = bool(r["is_unique"]),
                is_readonly        = bool(r["is_readonly"]),
                editable_on_update = bool(r["editable_on_update"]),
                default_value      = r["default_value"],
                ref                = r["ref"],
                options            = r["options"],
                validation         = r["validation"],
                in_list            = bool(r["in_list"]),
                in_form            = bool(r["in_form"]),
                sort_order         = int(r["sort_order"] or 0),
            )

        return ObjectMeta(
            id            = obj_row["id"],
            code          = obj_row["code"],
            api_slug      = obj_row["api_slug"],
            resource_code = obj_row["resource_code"],
            schema_name   = obj_row["schema_name"],
            object_name = obj_row["object_name"],
            kind        = obj_row["kind"],
            name        = obj_row["name"] or {},
            label_field = obj_row["label_field"],
            pk_field    = obj_row["pk_field"] or "id",
            actions     = obj_row["actions"] or {},
            policy      = obj_row["policy"] or {},
            fields      = [_to_field(r) for r in field_rows],
        )


# Module-level singleton shared across all requests in the process
registry = RegistryCache()
