"""Shared fixtures for API engine tests.

Unit tests (no DB) can run anywhere.
Integration tests require DATABASE_URL in the environment and are marked
with @pytest.mark.integration.
"""
import os
import pytest
import asyncpg

from app.modules.api_bridge.engine.registry import FieldMeta, ObjectMeta


# ── Unit-test helpers ─────────────────────────────────────────────────────────

def make_field(
    field_name: str,
    field_type: str = "text",
    *,
    is_required:        bool  = False,
    is_unique:          bool  = False,
    is_readonly:        bool  = False,
    editable_on_update: bool  = True,
    in_list:            bool  = True,
    in_form:            bool  = True,
    ref:                dict  | None = None,
    options:            list  | None = None,
    validation:         dict  | None = None,
    sort_order:         int   = 0,
    default_value             = None,
) -> FieldMeta:
    return FieldMeta(
        id                 = 1,
        code               = f"test.obj.{field_name}",
        object_code        = "test.obj",
        field_name         = field_name,
        field_type         = field_type,
        name               = {"en": field_name.replace("_", " ").title()},
        description        = None,
        is_required        = is_required,
        is_unique          = is_unique,
        is_readonly        = is_readonly,
        editable_on_update = editable_on_update,
        default_value      = default_value,
        ref                = ref,
        options            = options,
        validation         = validation,
        in_list            = in_list,
        in_form            = in_form,
        sort_order         = sort_order,
    )


def make_object(
    object_name: str,
    fields:      list[FieldMeta],
    *,
    schema:      str  = "test",
    kind:        str  = "table",
    pk_field:    str  = "id",
    actions:     dict | None = None,
    policy:      dict | None = None,
) -> ObjectMeta:
    return ObjectMeta(
        id            = 1,
        code          = f"{schema}.{object_name}",
        api_slug      = object_name,
        resource_code = None,
        schema_name   = schema,
        object_name = object_name,
        kind        = kind,
        name        = {"en": object_name.replace("_", " ").title()},
        label_field = "code",
        pk_field    = pk_field,
        actions     = actions or {
            "list":   {"impl": "generic"},
            "get":    {"impl": "generic"},
            "insert": {"impl": "generic"},
            "update": {"impl": "generic"},
            "delete": {"impl": "generic"},
        },
        policy = policy or {
            "roles":         {"list": ["*"], "get": ["*"], "insert": ["admin", "writer"],
                              "update": ["admin", "writer"], "delete": ["admin"]},
            "default_limit": 25,
            "max_limit":     200,
            "max_affected":  500,
        },
        fields = fields,
    )


# ── Integration fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping integration tests")
    return url


@pytest.fixture(scope="session")
async def db_conn(db_url: str):
    conn = await asyncpg.connect(db_url)
    yield conn
    await conn.close()
