"""Idempotent DDL for the API engine registry tables and pim helpers.

Called from app/db/setup.py -> run_seeds() on every startup.
All statements use IF NOT EXISTS / OR REPLACE so they are safe to re-run.
"""
from __future__ import annotations

import asyncpg


async def setup_engine(conn: asyncpg.Connection) -> None:
    print("[setup] ── engine registry setup ──────────────────────────────────────")

    # ── pim schema (may already exist from toolkit) ───────────────────────────
    await conn.execute("CREATE SCHEMA IF NOT EXISTS pim")
    # NOTE: pim.locales is a user-managed table — it is NOT auto-created here.
    # Create it through the toolkit UI or your own migration (e.g. pim_database.sql).

    # ── pim.tr() — multilingual JSONB resolver ────────────────────────────────
    await conn.execute("""
        CREATE OR REPLACE FUNCTION pim.tr(
            field jsonb,
            want  text,
            fb    text DEFAULT 'en'
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        AS $$
            SELECT COALESCE(field->>want, field->>fb)
        $$
    """)
    print("[setup]   pim.tr() ✓")

    # ── toolkit.api_objects ───────────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS toolkit.api_objects (
            id            BIGSERIAL PRIMARY KEY,
            code          TEXT NOT NULL,
            api_slug      TEXT,
            resource_code TEXT,
            schema_name   TEXT NOT NULL,
            object_name  TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'table'
                         CHECK (kind IN ('table', 'view', 'function')),
            "name"       JSONB NOT NULL DEFAULT '{"en":""}',
            label_field  TEXT,
            pk_field     TEXT NOT NULL DEFAULT 'id',
            actions      JSONB NOT NULL DEFAULT '{}',
            policy       JSONB NOT NULL DEFAULT '{}',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            inserted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            inserted_by  TEXT,
            modified_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            modified_by  TEXT,
            CONSTRAINT uq_api_objects_code UNIQUE (code)
        )
    """)
    # Migrations: add columns to pre-existing tables
    await conn.execute(
        "ALTER TABLE toolkit.api_objects ADD COLUMN IF NOT EXISTS api_slug TEXT"
    )
    await conn.execute(
        "ALTER TABLE toolkit.api_objects ADD COLUMN IF NOT EXISTS resource_code TEXT"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_objects_slug"
        " ON toolkit.api_objects (api_slug) WHERE api_slug IS NOT NULL"
    )
    # Backfill: any row without a slug gets object_name as default
    await conn.execute(
        "UPDATE toolkit.api_objects SET api_slug = object_name WHERE api_slug IS NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_objects_active"
        " ON toolkit.api_objects (is_active)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_objects_schema"
        " ON toolkit.api_objects (schema_name)"
    )
    print("[setup]   toolkit.api_objects ✓")

    # ── toolkit.api_fields ────────────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS toolkit.api_fields (
            id                 BIGSERIAL PRIMARY KEY,
            code               TEXT NOT NULL,
            object_code        TEXT NOT NULL
                               REFERENCES toolkit.api_objects(code) ON DELETE CASCADE,
            field_name         TEXT NOT NULL,
            field_type         TEXT NOT NULL DEFAULT 'text'
                               CHECK (field_type IN (
                                   'text', 'number', 'decimal', 'boolean',
                                   'date', 'timestamp', 'json',
                                   'i18n_text', 'i18n_richtext',
                                   'reference', 'select'
                               )),
            "name"             JSONB NOT NULL DEFAULT '{"en":""}',
            description        JSONB,
            is_required        BOOLEAN NOT NULL DEFAULT FALSE,
            is_unique          BOOLEAN NOT NULL DEFAULT FALSE,
            is_readonly        BOOLEAN NOT NULL DEFAULT FALSE,
            editable_on_update BOOLEAN NOT NULL DEFAULT TRUE,
            default_value      JSONB,
            ref                JSONB,
            options            JSONB,
            validation         JSONB,
            in_list            BOOLEAN NOT NULL DEFAULT TRUE,
            in_form            BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order         INTEGER NOT NULL DEFAULT 0,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            inserted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            inserted_by        TEXT,
            modified_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            modified_by        TEXT,
            CONSTRAINT uq_api_fields_code UNIQUE (code),
            CONSTRAINT uq_api_fields_object_field UNIQUE (object_code, field_name)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_fields_object"
        " ON toolkit.api_fields (object_code)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_fields_active"
        " ON toolkit.api_fields (is_active)"
    )
    print("[setup]   toolkit.api_fields ✓")

    # ── toolkit.fn_register_table() — bootstrap helper ────────────────────────
    # Introspects information_schema and inserts/updates api_objects + api_fields
    # for an existing physical table.  Safe to call multiple times.
    await conn.execute(r"""
        CREATE OR REPLACE FUNCTION toolkit.fn_register_table(
            p_schema      TEXT,
            p_table       TEXT,
            p_name_en     TEXT DEFAULT NULL,
            p_actions     JSONB DEFAULT '{"list":{"impl":"generic"},"get":{"impl":"generic"},"insert":{"impl":"generic"},"update":{"impl":"generic"},"delete":{"impl":"generic"}}',
            p_policy      JSONB DEFAULT '{"roles":{"list":["*"],"get":["*"],"insert":["admin","writer"],"update":["admin","writer"],"delete":["admin"]},"default_limit":25,"max_limit":200,"max_affected":500}'
        )
        RETURNS TEXT
        LANGUAGE plpgsql
        AS $fn$
        DECLARE
            v_code       TEXT := p_schema || '.' || p_table;
            v_name_en    TEXT := COALESCE(p_name_en, initcap(replace(p_table, '_', ' ')));
            v_obj_id     BIGINT;
            v_field_code TEXT;
            v_sort       INT := 0;
            col          RECORD;
        BEGIN
            -- Upsert api_objects
            INSERT INTO toolkit.api_objects
                (code, api_slug, schema_name, object_name, kind, "name", actions, policy, is_active)
            VALUES
                (v_code, p_table, p_schema, p_table, 'table',
                 jsonb_build_object('en', v_name_en),
                 p_actions, p_policy, TRUE)
            ON CONFLICT (code) DO UPDATE
                SET schema_name = EXCLUDED.schema_name,
                    object_name = EXCLUDED.object_name,
                    -- preserve a customised slug; only fill if still null
                    api_slug    = COALESCE(toolkit.api_objects.api_slug, EXCLUDED.api_slug),
                    is_active   = TRUE;

            SELECT id INTO v_obj_id FROM toolkit.api_objects WHERE code = v_code;

            -- Deactivate fields that no longer exist in the physical table
            UPDATE toolkit.api_fields af
            SET is_active = FALSE
            WHERE af.object_code = v_code
              AND af.field_name NOT IN (
                  SELECT column_name FROM information_schema.columns
                  WHERE table_schema = p_schema AND table_name = p_table
              );

            -- Insert/update fields from information_schema
            v_sort := 0;
            FOR col IN
                SELECT column_name, data_type, ordinal_position,
                       column_default, is_nullable
                FROM   information_schema.columns
                WHERE  table_schema = p_schema AND table_name = p_table
                ORDER  BY ordinal_position
            LOOP
                v_sort        := v_sort + 10;
                v_field_code  := v_code || '.' || col.column_name;

                INSERT INTO toolkit.api_fields
                    (code, object_code, field_name, field_type, "name",
                     is_readonly, editable_on_update, in_list, in_form,
                     sort_order, is_active)
                VALUES (
                    v_field_code,
                    v_code,
                    col.column_name,
                    CASE
                        WHEN col.column_name IN ('inserted_at','modified_at') THEN 'timestamp'
                        WHEN col.column_name IN ('inserted_by','modified_by') THEN 'text'
                        WHEN col.data_type IN ('integer','bigint','smallint','numeric','real','double precision') THEN 'number'
                        WHEN col.data_type IN ('boolean') THEN 'boolean'
                        WHEN col.data_type IN ('date') THEN 'date'
                        WHEN col.data_type IN ('timestamp with time zone','timestamp without time zone') THEN 'timestamp'
                        WHEN col.data_type IN ('jsonb','json') THEN 'json'
                        ELSE 'text'
                    END,
                    jsonb_build_object('en', initcap(replace(col.column_name, '_', ' '))),
                    col.column_name IN ('id','inserted_at','modified_at','inserted_by','modified_by'),
                    col.column_name NOT IN ('id','inserted_at','inserted_by'),
                    TRUE,
                    col.column_name NOT IN ('inserted_at','inserted_by','modified_at','modified_by'),
                    v_sort,
                    TRUE
                )
                ON CONFLICT (object_code, field_name) DO UPDATE
                    SET is_active  = TRUE,
                        sort_order = EXCLUDED.sort_order;
            END LOOP;

            RETURN v_code;
        END
        $fn$
    """)
    print("[setup]   toolkit.fn_register_table() ✓")

    print("[setup] ── engine registry ready ──────────────────────────────────────")
