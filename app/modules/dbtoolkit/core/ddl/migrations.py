"""
Module: toolkit.ddl.migrations
Purpose: ALTER TABLE helpers for column add, alter, drop, and rename operations
         on toolkit-managed PostgreSQL tables.

All helpers return SQL strings only — execution is handled by exec_ddl()
in the executor module.  Every function is designed to be idempotent where
possible (using IF EXISTS / IF NOT EXISTS guards).
"""

import json as _json

from app.modules.dbtoolkit.core.constants import DEFAULT_SCHEMA
from app.modules.dbtoolkit.core.ddl.types import pg_type, safe_name


def _qi(name: str) -> str:
    """Double-quote a SQL identifier to handle reserved keywords."""
    return f'"{name}"'


def build_add_column(table: dict, field: dict) -> str:
    """Generate SQL to add a new column to an existing table.

    Handles special cases:
    - uuid: idempotent DO block that drops/re-adds if the type is wrong.
    - sequence: DO block with a companion PostgreSQL sequence object.
    - toggle: adds NOT NULL DEFAULT FALSE.
    - numeric/integer/bigint: adds DEFAULT 0 to satisfy NOT NULL on live tables.
    - Text-like types: adds DEFAULT '' when no explicit default is set.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.
        field: Toolkit field dict with 'field_type', 'code', and optional config.

    Returns:
        SQL string (ALTER TABLE or DO block).

    Raises:
        ValueError: If the field type produces no physical column, or if trying
            to add a primary-key id column after table creation.
    """
    tbl, qual = _qual(table)
    fc      = safe_name(field["code"])
    pgt     = pg_type(field)
    if pgt is None:
        raise ValueError(f"Field type {field['field_type']} adds no column")
    if field["field_type"] == "id" and fc == "id":
        raise ValueError("Cannot add an auto-increment primary key after table creation")
    # id type on a non-PK column → bigint (no serial / no PK constraint)
    if field["field_type"] == "id" and fc != "id":
        pgt = "bigint"

    schema = safe_name(table.get("schema_name") or DEFAULT_SCHEMA)

    if field["field_type"] == "uuid":
        return (
            f"do $$ begin\n"
            f"  if exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}' and udt_name<>'uuid') then\n"
            f"    execute 'alter table {qual} drop column {_qi(fc)} cascade';\n"
            f"  end if;\n"
            f"  if not exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}') then\n"
            f"    execute 'alter table {qual} add column {_qi(fc)} uuid default gen_random_uuid()';\n"
            f"  end if;\n"
            f"  update {qual} set {_qi(fc)} = gen_random_uuid() where {_qi(fc)} is null;\n"
            f"end $$;"
        )

    if field["field_type"] == "sequence":
        seq_name = f"{tbl}_{fc}_seq"
        # Schema-qualify so the sequence lives in the same schema as the table.
        # Without this, 'ALTER SEQUENCE … OWNED BY pim.table.col' fails because
        # the unqualified sequence lands in public (different schema).
        seq_qual = f"{schema}.{seq_name}" if schema != "public" else seq_name
        return (
            f"do $$ begin\n"
            f"  if exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}' and udt_name<>'int8') then\n"
            f"    execute 'alter table {qual} drop column {_qi(fc)} cascade';\n"
            f"  end if;\n"
            f"  execute 'create sequence if not exists {seq_qual}';\n"
            f"  if not exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}') then\n"
            f"    execute 'alter table {qual} add column {_qi(fc)} bigint default nextval(''{seq_qual}'')';\n"
            f"    execute 'alter sequence {seq_qual} owned by {qual}.{_qi(fc)}';\n"
            f"  end if;\n"
            f"  update {qual} set {_qi(fc)} = nextval('{seq_qual}') where {_qi(fc)} is null;\n"
            f"end $$;"
        )

    col = f"alter table {qual} add column if not exists {_qi(fc)} {pgt}"

    dv = _col_default(field)
    if dv:
        col += dv
    else:
        ftype = field["field_type"]
        if ftype == "toggle":
            col += " not null default false"
        elif pgt in ("integer", "bigint", "bigserial"):
            col += " default 0"
        elif pgt.startswith("numeric"):
            col += " default 0"
        elif ftype in ("date", "datetime", "time"):
            pass  # nullable is fine for date columns
        elif pgt in ("json", "jsonb"):
            if field.get("is_multilingual"):
                col += " default '{}'"  # multilingual: start with empty translations object
            # plain json/jsonb fields: leave nullable
        elif ftype == "text_array" or pgt == "text[]":
            col += " default '{}'"  # empty array; no cast needed — pg accepts untyped '{}'
        else:
            col += " default ''"  # text-like types

    col += ";"

    if field.get("description"):
        desc = field["description"].replace("'", "''")
        col += f"\ncomment on column {qual}.{_qi(fc)} is '{desc}';"
    return col


def build_alter_column(table: dict, field: dict) -> str:
    """Generate SQL to change the type of an existing column.

    Handles uuid and sequence via the same idempotent DO block pattern as
    build_add_column (drop-if-wrong-type, add-if-missing).  For all other types,
    emits ALTER COLUMN ... TYPE ... USING cast.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.
        field: Updated toolkit field dict.

    Returns:
        SQL string.

    Raises:
        ValueError: If the new field type produces no physical column.
    """
    tbl, qual = _qual(table)
    fc      = safe_name(field["code"])
    ftype   = field["field_type"]
    pgt     = pg_type(field)
    schema  = safe_name(table.get("schema_name") or DEFAULT_SCHEMA)
    if pgt is None:
        raise ValueError(f"Field type {field['field_type']} has no physical column")

    if ftype == "uuid":
        return (
            f"do $$ begin\n"
            f"  if exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}' and udt_name<>'uuid') then\n"
            f"    execute 'alter table {qual} drop column {_qi(fc)} cascade';\n"
            f"  end if;\n"
            f"  if not exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}') then\n"
            f"    execute 'alter table {qual} add column {_qi(fc)} uuid default gen_random_uuid()';\n"
            f"  end if;\n"
            f"  update {qual} set {_qi(fc)} = gen_random_uuid() where {_qi(fc)} is null;\n"
            f"end $$;"
        )

    if ftype == "sequence":
        seq_name = f"{tbl}_{fc}_seq"
        seq_qual = f"{schema}.{seq_name}" if schema != "public" else seq_name
        return (
            f"do $$ begin\n"
            f"  if exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}' and udt_name<>'int8') then\n"
            f"    execute 'alter table {qual} drop column {_qi(fc)} cascade';\n"
            f"  end if;\n"
            f"  execute 'create sequence if not exists {seq_qual}';\n"
            f"  if not exists (select 1 from information_schema.columns\n"
            f"    where table_schema='{schema}' and table_name='{tbl}'\n"
            f"    and column_name='{fc}') then\n"
            f"    execute 'alter table {qual} add column {_qi(fc)} bigint default nextval(''{seq_qual}'')';\n"
            f"    execute 'alter sequence {seq_qual} owned by {qual}.{_qi(fc)}';\n"
            f"  end if;\n"
            f"  update {qual} set {_qi(fc)} = nextval('{seq_qual}') where {_qi(fc)} is null;\n"
            f"end $$;"
        )

    # Drop the generated view before any type change — Postgres refuses ALTER COLUMN
    # when a view's rule references the column.  The view is always recreated by the
    # caller (update_table / regenerate_view) after all field changes are applied.
    drop_view = f"drop view if exists {schema}.v_{tbl} cascade;"

    # For multilingual text→jsonb migration: wrap existing text as {"en": "<value>"}
    # so no data is lost.  A plain USING col::jsonb fails on non-JSON text values.
    if field.get("is_multilingual") and pgt == "jsonb":
        using = (
            f"CASE WHEN {_qi(fc)} IS NULL OR {_qi(fc)} = '' "
            f"THEN '{{}}'::jsonb "
            f"ELSE jsonb_build_object('en', {_qi(fc)}) END"
        )
        return f"{drop_view}\nalter table {qual} alter column {_qi(fc)} type jsonb using {using};"

    return f"{drop_view}\nalter table {qual} alter column {_qi(fc)} type {pgt} using {_qi(fc)}::{pgt};"


def build_drop_column(table: dict, field_code: str) -> str:
    """Generate SQL to drop a column from a table (IF EXISTS CASCADE).

    CASCADE is required so that any view or function that references the column
    is dropped automatically. The caller (delete_field) rebuilds them via
    reload_table_views after the field is removed from the catalog.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.
        field_code: The field code (will be safe_name'd).

    Returns:
        ALTER TABLE DROP COLUMN IF EXISTS ... CASCADE SQL string.
    """
    _, qual = _qual(table)
    fc = safe_name(field_code)
    return f"alter table {qual} drop column if exists {_qi(fc)} cascade;"


def build_rename_column(table: dict, old_code: str, new_code: str) -> str:
    """Generate SQL to rename a column.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.
        old_code: Current field code.
        new_code: New field code.

    Returns:
        ALTER TABLE RENAME COLUMN SQL string.
    """
    _, qual = _qual(table)
    return f"alter table {qual} rename column {_qi(safe_name(old_code))} to {_qi(safe_name(new_code))};"


def build_drop_table(table: dict) -> str:
    """Generate SQL to drop a table (IF EXISTS, CASCADE).

    Args:
        table: Table dict with 'code' and optional 'schema_name'.

    Returns:
        DROP TABLE IF EXISTS ... CASCADE SQL string.
    """
    _, qual = _qual(table)
    return f"drop table if exists {qual} cascade;"


# ---------------------------------------------------------------------------
# Private helpers (same as builder.py — local copies to avoid circular imports)
# ---------------------------------------------------------------------------

def _col_default(f: dict) -> str:
    """Return a ' default ...' SQL fragment for a field, or empty string."""
    dv = f.get("default_value")
    if dv is None:
        return ""
    if f.get("is_multilingual"):
        return ""  # jsonb multilingual columns: default handled separately as '{}'
    ftype = f["field_type"]
    if ftype in ("text", "textarea", "richtext", "slug", "email", "phone", "url",
                 "color", "file", "image", "inline_select", "select"):
        return f" default {dv!r}"
    if ftype == "sequence":
        return ""
    if ftype == "toggle":
        return f" default {str(dv).lower()}"
    if ftype in ("json", "jsonb"):
        if isinstance(dv, (dict, list)):
            return f" default '{_json.dumps(dv)}'::jsonb"
        dv_str = str(dv).strip()
        try:
            _json.loads(dv_str)
        except (ValueError, TypeError):
            dv_str = "{}"
        return f" default '{dv_str}'::jsonb"
    if ftype == "text_array":
        return " default '{}'"
    return f" default {dv}"


def _qual(table: dict) -> tuple[str, str]:
    """Return (tbl, qualified_name) for the table."""
    tbl    = safe_name(table["code"])
    schema = safe_name(table.get("schema_name") or DEFAULT_SCHEMA)
    qual   = f"{schema}.{tbl}" if schema != DEFAULT_SCHEMA else tbl
    return tbl, qual
