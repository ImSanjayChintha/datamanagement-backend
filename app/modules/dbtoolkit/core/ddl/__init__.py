"""
Package: dbtoolkit.core.ddl
Purpose: DDL generation for toolkit-managed PostgreSQL objects.
  types      — Field type mapping and PostgreSQL type resolution.
  builder    — SQL builders for CREATE TABLE / VIEW / FUNCTION.
  migrations — ALTER TABLE helpers for column add / alter / drop / rename.
"""
from app.modules.dbtoolkit.core.ddl.types import (
    PG_TYPES, FIELD_TYPES_NO_COLUMN, pg_type, safe_name, is_field_multilingual,
)
from app.modules.dbtoolkit.core.ddl.builder import (
    build_create_table, build_create_view, build_list_function,
    build_get_function, build_upsert_function, build_delete_function,
    build_bulk_insert_function, build_full_ddl,
)
from app.modules.dbtoolkit.core.ddl.migrations import (
    build_add_column, build_alter_column, build_drop_column,
    build_rename_column, build_drop_table,
)

# Backward-compat aliases
_pg_type               = pg_type
_safe_name             = safe_name
_is_field_multilingual = is_field_multilingual
_NO_COLUMN_TYPES       = FIELD_TYPES_NO_COLUMN

__all__ = [
    "PG_TYPES", "FIELD_TYPES_NO_COLUMN", "pg_type", "safe_name", "is_field_multilingual",
    "build_create_table", "build_create_view", "build_list_function",
    "build_get_function", "build_upsert_function", "build_delete_function",
    "build_bulk_insert_function", "build_full_ddl",
    "build_add_column", "build_alter_column", "build_drop_column",
    "build_rename_column", "build_drop_table",
    "_pg_type", "_safe_name", "_is_field_multilingual", "_NO_COLUMN_TYPES",
]
