-- ════════════════════════════════════════════════════════════════════════════
-- schema.sql  —  GMC ecommerce_platform
--
-- Source files:
--   app/db/setup.py
--   app/modules/api_bridge/resources/db/setup.py
--   app/modules/api_bridge/gateway/db/setup.py
--   app/modules/api_bridge/engine/db_setup.py
--   app/modules/push_destinations/db/setup.py
--   app/modules/company/db/setup.py
--
-- Safe to re-run: IF NOT EXISTS / OR REPLACE / DROP VIEW IF EXISTS CASCADE
-- throughout.  Seed / reference data is NOT included — populate via the UI
-- or a separate seed script.
--
-- Prerequisites:
--   The toolkit module's base tables (toolkit.toolkit_tables,
--   toolkit.toolkit_fields, toolkit.toolkit_field_options) must exist before
--   the ALTER TABLE statements in §3b and the views in §5 are executed.
--   Run the toolkit base DDL first on truly empty databases.
--
-- Order: §1 schemas → §2 admin → §3 toolkit → §4 api_gateway → §5 views → §6 functions
-- ════════════════════════════════════════════════════════════════════════════


-- ── §1. Schemas ──────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS api_gateway;
CREATE SCHEMA IF NOT EXISTS company;
CREATE SCHEMA IF NOT EXISTS pim;
CREATE SCHEMA IF NOT EXISTS toolkit;


-- ── §2. Admin ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS admin.admin_users (
    id                      BIGSERIAL    PRIMARY KEY,
    email                   TEXT         NOT NULL UNIQUE,
    full_name               TEXT,
    role                    TEXT         NOT NULL DEFAULT 'admin'
                                         CHECK (role IN ('admin','local_admin','writer','reader')),
    hashed_password         TEXT         NOT NULL,
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    must_change_password    BOOLEAN      NOT NULL DEFAULT FALSE,
    invite_token            TEXT,
    invite_token_expires_at TIMESTAMPTZ,
    last_login_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ
);


-- ── §3a. Toolkit — new infrastructure tables ─────────────────────────────────
-- These tables are owned by this application, not by the toolkit module.

CREATE TABLE IF NOT EXISTS toolkit.toolkit_field_references (
    id                BIGSERIAL PRIMARY KEY,
    field_id          BIGINT    NOT NULL
                      REFERENCES toolkit.toolkit_fields(id) ON DELETE CASCADE,
    ref_table_id      BIGINT    NOT NULL
                      REFERENCES toolkit.toolkit_tables(id) ON DELETE RESTRICT,
    store_field       TEXT      NOT NULL DEFAULT 'code',
    display_field     TEXT      NOT NULL DEFAULT 'label',
    display_template  TEXT,
    search_fields     TEXT[]    DEFAULT ARRAY['code','label'],
    filter_conditions JSONB,
    order_by          TEXT      DEFAULT 'sort_order, code',
    ref_type          TEXT      NOT NULL DEFAULT 'single',
    cascade_on_delete TEXT      NOT NULL DEFAULT 'restrict',
    CONSTRAINT uq_toolkit_field_ref_field UNIQUE (field_id)
);

CREATE TABLE IF NOT EXISTS toolkit.toolkit_activity_log (
    id            BIGSERIAL   PRIMARY KEY,
    performed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    performed_by  TEXT,
    activity_type TEXT        NOT NULL,
    entity_type   TEXT        NOT NULL,
    entity_id     BIGINT,
    entity_code   TEXT,
    schema_name   TEXT,
    table_code    TEXT,
    detail        JSONB
);

CREATE INDEX IF NOT EXISTS idx_activity_log_at
    ON toolkit.toolkit_activity_log (performed_at DESC);

CREATE INDEX IF NOT EXISTS idx_activity_log_entity
    ON toolkit.toolkit_activity_log (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_by
    ON toolkit.toolkit_activity_log (performed_by);

CREATE INDEX IF NOT EXISTS idx_activity_log_table_code
    ON toolkit.toolkit_activity_log (table_code);

CREATE TABLE IF NOT EXISTS toolkit.page_definitions (
    id              BIGSERIAL   PRIMARY KEY,
    code            TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    description     TEXT,
    icon            TEXT,
    table_code      TEXT        NOT NULL,
    gateway_object  TEXT        NOT NULL,
    id_type         TEXT        NOT NULL DEFAULT 'string'
                                CHECK (id_type IN ('string', 'number')),
    nav_section     TEXT        NOT NULL DEFAULT 'pim',
    nav_label       TEXT,
    nav_order       INTEGER     NOT NULL DEFAULT 0,
    list_config     JSONB       NOT NULL DEFAULT '{"columns":[]}',
    form_config     JSONB       NOT NULL DEFAULT '{"fields":[]}',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by     TEXT,
    modified_at     TIMESTAMPTZ,
    modified_by     TEXT,
    list_endpoint   TEXT        NOT NULL DEFAULT '',
    upsert_endpoint TEXT        NOT NULL DEFAULT '',
    delete_endpoint TEXT        NOT NULL DEFAULT '',
    CONSTRAINT uq_page_definitions_code UNIQUE (code)
);

-- Catch-up for instances created before the endpoint-override columns were added
ALTER TABLE toolkit.page_definitions
    ADD COLUMN IF NOT EXISTS list_endpoint   TEXT NOT NULL DEFAULT '';
ALTER TABLE toolkit.page_definitions
    ADD COLUMN IF NOT EXISTS upsert_endpoint TEXT NOT NULL DEFAULT '';
ALTER TABLE toolkit.page_definitions
    ADD COLUMN IF NOT EXISTS delete_endpoint TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS toolkit.common_fields (
    id               BIGSERIAL   PRIMARY KEY,
    code             TEXT        NOT NULL,
    sort_order       INTEGER     NOT NULL DEFAULT 0,
    field_name       TEXT        NOT NULL,
    data_type        TEXT        NOT NULL
                     CHECK (data_type IN (
                         'text','number','integer','boolean','date','datetime',
                         'select','multiselect','json','url','email','phone','color'
                     )),
    field_type       TEXT        NOT NULL DEFAULT 'text',
    field_role       TEXT        NOT NULL DEFAULT 'user'
                     CHECK (field_role IN ('user', 'log')),
    show_translation BOOLEAN     NOT NULL DEFAULT TRUE,
    default_value    TEXT,
    true_label       TEXT,
    false_label      TEXT,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    inserted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by      TEXT,
    modified_at      TIMESTAMPTZ,
    modified_by      TEXT,
    CONSTRAINT uq_common_fields_code UNIQUE (code)
);

-- Catch-up for instances created before these columns were added
ALTER TABLE toolkit.common_fields ADD COLUMN IF NOT EXISTS default_value TEXT;
ALTER TABLE toolkit.common_fields ADD COLUMN IF NOT EXISTS true_label    TEXT;
ALTER TABLE toolkit.common_fields ADD COLUMN IF NOT EXISTS false_label   TEXT;
ALTER TABLE toolkit.common_fields ADD COLUMN IF NOT EXISTS field_type    TEXT NOT NULL DEFAULT 'text';

CREATE INDEX IF NOT EXISTS idx_common_fields_code
    ON toolkit.common_fields (code);

CREATE INDEX IF NOT EXISTS idx_common_fields_active
    ON toolkit.common_fields (is_active);

CREATE TABLE IF NOT EXISTS toolkit.common_fields_translation (
    id              BIGSERIAL   PRIMARY KEY,
    common_field_id BIGINT      NOT NULL DEFAULT 0,
    lang            TEXT        NOT NULL DEFAULT 'en',
    label           TEXT        NOT NULL DEFAULT '',
    description     TEXT,
    true_label      TEXT,
    false_label     TEXT,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by     TEXT,
    modified_at     TIMESTAMPTZ,
    modified_by     TEXT
);

-- Catch-up columns for pre-existing installations
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS common_field_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS lang            TEXT   NOT NULL DEFAULT 'en';
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS label           TEXT   NOT NULL DEFAULT '';
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS description     TEXT;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS true_label      TEXT;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS false_label     TEXT;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS inserted_by     TEXT;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS modified_at     TIMESTAMPTZ;
ALTER TABLE toolkit.common_fields_translation ADD COLUMN IF NOT EXISTS modified_by     TEXT;

-- FK and UNIQUE constraints — wrapped in DO blocks so re-running is safe
DO $$ BEGIN
    ALTER TABLE toolkit.common_fields_translation
        ADD CONSTRAINT fk_cft_field FOREIGN KEY (common_field_id)
        REFERENCES toolkit.common_fields(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE toolkit.common_fields_translation
        ADD CONSTRAINT uq_common_fields_translation UNIQUE (common_field_id, lang);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_cft_field_lang
    ON toolkit.common_fields_translation (common_field_id, lang);


-- ── §3b. Toolkit — alterations to toolkit-module tables ──────────────────────
-- These statements extend tables owned by the toolkit module.
-- They are no-ops on fresh installs where the columns were added at table-
-- creation time; they catch up older deployments that pre-date these columns.

ALTER TABLE toolkit.toolkit_field_options
    ADD COLUMN IF NOT EXISTS label_i18n JSONB NOT NULL DEFAULT '{}';

ALTER TABLE toolkit.toolkit_fields
    ADD COLUMN IF NOT EXISTS ref_table_code TEXT,
    ADD COLUMN IF NOT EXISTS store_field     TEXT,
    ADD COLUMN IF NOT EXISTS display_field   TEXT,
    ADD COLUMN IF NOT EXISTS ref_type        TEXT DEFAULT 'single';


-- ── §3c. Toolkit — engine registry tables ────────────────────────────────────

CREATE TABLE IF NOT EXISTS toolkit.api_objects (
    id            BIGSERIAL   PRIMARY KEY,
    code          TEXT        NOT NULL,
    api_slug      TEXT,
    resource_code TEXT,
    schema_name   TEXT        NOT NULL,
    object_name   TEXT        NOT NULL,
    kind          TEXT        NOT NULL DEFAULT 'table'
                  CHECK (kind IN ('table', 'view', 'function')),
    "name"        JSONB       NOT NULL DEFAULT '{"en":""}',
    label_field   TEXT,
    pk_field      TEXT        NOT NULL DEFAULT 'id',
    actions       JSONB       NOT NULL DEFAULT '{}',
    policy        JSONB       NOT NULL DEFAULT '{}',
    sort_order    INTEGER     NOT NULL DEFAULT 0,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    inserted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by   TEXT,
    modified_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_by   TEXT,
    CONSTRAINT uq_api_objects_code UNIQUE (code)
);

-- Catch-up for pre-existing tables
ALTER TABLE toolkit.api_objects ADD COLUMN IF NOT EXISTS api_slug      TEXT;
ALTER TABLE toolkit.api_objects ADD COLUMN IF NOT EXISTS resource_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_api_objects_slug
    ON toolkit.api_objects (api_slug) WHERE api_slug IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_api_objects_active
    ON toolkit.api_objects (is_active);

CREATE INDEX IF NOT EXISTS idx_api_objects_schema
    ON toolkit.api_objects (schema_name);

CREATE TABLE IF NOT EXISTS toolkit.api_fields (
    id                 BIGSERIAL   PRIMARY KEY,
    code               TEXT        NOT NULL,
    object_code        TEXT        NOT NULL
                       REFERENCES toolkit.api_objects(code) ON DELETE CASCADE,
    field_name         TEXT        NOT NULL,
    field_type         TEXT        NOT NULL DEFAULT 'text'
                       CHECK (field_type IN (
                           'text', 'number', 'decimal', 'boolean',
                           'date', 'timestamp', 'json',
                           'i18n_text', 'i18n_richtext',
                           'reference', 'select'
                       )),
    "name"             JSONB       NOT NULL DEFAULT '{"en":""}',
    description        JSONB,
    is_required        BOOLEAN     NOT NULL DEFAULT FALSE,
    is_unique          BOOLEAN     NOT NULL DEFAULT FALSE,
    is_readonly        BOOLEAN     NOT NULL DEFAULT FALSE,
    editable_on_update BOOLEAN     NOT NULL DEFAULT TRUE,
    default_value      JSONB,
    ref                JSONB,
    options            JSONB,
    validation         JSONB,
    in_list            BOOLEAN     NOT NULL DEFAULT TRUE,
    in_form            BOOLEAN     NOT NULL DEFAULT TRUE,
    sort_order         INTEGER     NOT NULL DEFAULT 0,
    is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
    inserted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by        TEXT,
    modified_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_by        TEXT,
    CONSTRAINT uq_api_fields_code         UNIQUE (code),
    CONSTRAINT uq_api_fields_object_field UNIQUE (object_code, field_name)
);

CREATE INDEX IF NOT EXISTS idx_api_fields_object
    ON toolkit.api_fields (object_code);

CREATE INDEX IF NOT EXISTS idx_api_fields_active
    ON toolkit.api_fields (is_active);


-- ── §4. API Gateway ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_gateway.api_resources (
    id              BIGSERIAL   PRIMARY KEY,
    code            TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    description     TEXT,
    base_url        TEXT        NOT NULL,
    timeout_seconds INTEGER     NOT NULL DEFAULT 30,
    auth_type       TEXT        NOT NULL DEFAULT 'none'
                    CHECK (auth_type IN ('none', 'bearer', 'basic', 'api_key', 'oauth2')),
    auth_config     JSONB       NOT NULL DEFAULT '{}',
    default_headers JSONB       NOT NULL DEFAULT '{}',
    ssl_verify      BOOLEAN     NOT NULL DEFAULT TRUE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by     TEXT,
    modified_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_by     TEXT,
    CONSTRAINT uq_api_resources_code UNIQUE (code)
);

CREATE INDEX IF NOT EXISTS idx_api_resources_is_active
    ON api_gateway.api_resources (is_active);

CREATE INDEX IF NOT EXISTS idx_api_resources_auth_type
    ON api_gateway.api_resources (auth_type);

CREATE TABLE IF NOT EXISTS api_gateway.endpoints (
    id             BIGSERIAL   PRIMARY KEY,
    name           TEXT        NOT NULL,
    url_path       TEXT        NOT NULL,
    method         TEXT        NOT NULL DEFAULT 'GET'
                   CHECK (method IN ('GET','POST','PUT','PATCH','DELETE')),
    db_schema      TEXT,
    db_object      TEXT,
    db_type        TEXT,   -- CHECK constraint is applied via named constraint below
    headers        JSONB   NOT NULL DEFAULT '[]',
    body_schema    JSONB   NOT NULL DEFAULT '{}',
    filters        JSONB   NOT NULL DEFAULT '[]',
    columns        JSONB   NOT NULL DEFAULT '[]',
    description    TEXT,
    status         TEXT    NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','active','deprecated')),
    operation_type TEXT,   -- CHECK constraint applied below
    config         JSONB   NOT NULL DEFAULT '{}',
    inserted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_at    TIMESTAMPTZ,
    CONSTRAINT uq_gw_endpoint UNIQUE (method, url_path)
);

-- Catch-up columns (no-ops on fresh install; catch-up on upgrade)
ALTER TABLE api_gateway.endpoints
    ADD COLUMN IF NOT EXISTS operation_type TEXT;

ALTER TABLE api_gateway.endpoints
    ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}';

-- Named CHECK constraints — drop any auto-named remnant then re-add the
-- current version (expands db_type to include 'service').
ALTER TABLE api_gateway.endpoints
    DROP CONSTRAINT IF EXISTS endpoints_db_type_check;

ALTER TABLE api_gateway.endpoints
    ADD CONSTRAINT endpoints_db_type_check
    CHECK (db_type IN ('table','view','function','service'));

DO $$ BEGIN
    ALTER TABLE api_gateway.endpoints
        ADD CONSTRAINT endpoints_operation_type_check
        CHECK (operation_type IN ('select','insert','update','delete'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_gw_ep_status
    ON api_gateway.endpoints (status);

CREATE INDEX IF NOT EXISTS idx_gw_ep_method_path
    ON api_gateway.endpoints (method, url_path);

CREATE TABLE IF NOT EXISTS api_gateway.push_destinations (
    id          BIGSERIAL   PRIMARY KEY,
    name        TEXT        NOT NULL,
    description TEXT,
    dest_type   TEXT        NOT NULL
                CHECK (dest_type IN (
                    'azure_blob', 'email', 's3_compatible', 'amazon_s3',
                    'filesystem', 'rabbitmq', 'sftp', 'http_api'
                )),
    config      JSONB       NOT NULL DEFAULT '{}',
    secrets     JSONB       NOT NULL DEFAULT '{}',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_by TEXT,
    modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_by TEXT,
    CONSTRAINT uq_push_destinations_name UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS idx_push_dest_is_active
    ON api_gateway.push_destinations (is_active);

CREATE INDEX IF NOT EXISTS idx_push_dest_type
    ON api_gateway.push_destinations (dest_type);


-- ── §5. Views ────────────────────────────────────────────────────────────────
-- All views use DROP … CASCADE so they can be safely re-run.  Any dependent
-- functions are recreated in §6 below.

-- ── admin ────────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS admin.v_admin_users CASCADE;
CREATE VIEW admin.v_admin_users AS
SELECT id, email, full_name, role, hashed_password, is_active,
       must_change_password, invite_token, invite_token_expires_at,
       last_login_at, created_at, updated_at
FROM   admin.admin_users
ORDER  BY created_at DESC;

-- ── toolkit ──────────────────────────────────────────────────────────────────
-- toolkit.toolkit_tables, toolkit.toolkit_fields are owned by the toolkit
-- module and assumed to exist at this point.

DROP VIEW IF EXISTS toolkit.v_toolkit_tables CASCADE;
CREATE VIEW toolkit.v_toolkit_tables AS
SELECT *
FROM   toolkit.toolkit_tables
ORDER  BY sort_order, code;

DROP VIEW IF EXISTS toolkit.v_toolkit_fields CASCADE;
CREATE VIEW toolkit.v_toolkit_fields AS
SELECT
    f.*,
    t.code      AS table_code,
    rt.id       AS ref_table_id,
    rt.label    AS ref_table_label,
    NULL::text  AS display_template,
    NULL::text  AS ref_order_by,
    NULL::text  AS cascade_on_delete
FROM   toolkit.toolkit_fields  f
JOIN   toolkit.toolkit_tables  t  ON t.id   = f.table_id
LEFT JOIN toolkit.toolkit_tables rt ON rt.code = f.ref_table_code
ORDER  BY f.sort_order, f.code;

DROP VIEW IF EXISTS toolkit.v_common_fields CASCADE;
CREATE VIEW toolkit.v_common_fields AS
SELECT
    cf.*,
    (SELECT label
     FROM   toolkit.common_fields_translation
     WHERE  common_field_id = cf.id AND lang = 'en'
     LIMIT  1) AS translated_label,
    (SELECT description
     FROM   toolkit.common_fields_translation
     WHERE  common_field_id = cf.id AND lang = 'en'
     LIMIT  1) AS translated_description
FROM   toolkit.common_fields cf
ORDER  BY cf.sort_order, cf.code;


-- ── §6. Functions ────────────────────────────────────────────────────────────

-- ── pim ──────────────────────────────────────────────────────────────────────

-- pim.tr() — resolve a multilingual JSONB field to a single text value.
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
$$;

-- ── admin ─────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION admin.fn_list_admin_users(
    p_role   TEXT    DEFAULT NULL,
    p_limit  INT     DEFAULT 50,
    p_offset INT     DEFAULT 0,
    p_search TEXT    DEFAULT NULL
) RETURNS SETOF admin.v_admin_users LANGUAGE sql STABLE AS $$
    SELECT * FROM admin.v_admin_users
    WHERE  (p_role   IS NULL OR role      =     p_role)
      AND  (p_search IS NULL
            OR email     ILIKE '%' || p_search || '%'
            OR full_name ILIKE '%' || p_search || '%')
    ORDER  BY created_at DESC
    LIMIT  p_limit OFFSET p_offset;
$$;

CREATE OR REPLACE FUNCTION admin.fn_get_admin_users(p_id BIGINT)
RETURNS SETOF admin.v_admin_users LANGUAGE sql STABLE AS $$
    SELECT * FROM admin.v_admin_users WHERE id = p_id;
$$;

CREATE OR REPLACE FUNCTION admin.fn_create_admin_user(
    p_email           TEXT,
    p_full_name       TEXT,
    p_hashed_password TEXT,
    p_role            TEXT,
    p_invite_token    TEXT
) RETURNS SETOF admin.v_admin_users LANGUAGE sql VOLATILE AS $$
    INSERT INTO admin.admin_users
        (email, full_name, hashed_password, role,
         must_change_password, invite_token, invite_token_expires_at, is_active)
    VALUES
        (p_email, p_full_name, p_hashed_password, p_role,
         TRUE, p_invite_token, NOW() + INTERVAL '7 days', TRUE)
    RETURNING
        id, email, full_name, role, hashed_password, is_active,
        must_change_password, invite_token, invite_token_expires_at,
        last_login_at, created_at, updated_at;
$$;

CREATE OR REPLACE FUNCTION admin.fn_resend_admin_invite(
    p_id              BIGINT,
    p_hashed_password TEXT,
    p_invite_token    TEXT
) RETURNS void LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password         = p_hashed_password,
           invite_token            = p_invite_token,
           invite_token_expires_at = NOW() + INTERVAL '7 days',
           updated_at              = NOW()
    WHERE  id = p_id;
$$;

CREATE OR REPLACE FUNCTION admin.fn_update_admin_user(
    p_id        BIGINT,
    p_full_name TEXT    DEFAULT NULL,
    p_is_active BOOLEAN DEFAULT NULL,
    p_role      TEXT    DEFAULT NULL
) RETURNS SETOF admin.v_admin_users LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    full_name  = COALESCE(p_full_name,  full_name),
           is_active  = COALESCE(p_is_active,  is_active),
           role       = COALESCE(p_role,        role),
           updated_at = NOW()
    WHERE  id = p_id
    RETURNING
        id, email, full_name, role, hashed_password, is_active,
        must_change_password, invite_token, invite_token_expires_at,
        last_login_at, created_at, updated_at;
$$;

CREATE OR REPLACE FUNCTION admin.fn_reset_admin_password(
    p_id              BIGINT,
    p_hashed_password TEXT
) RETURNS void LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password      = p_hashed_password,
           must_change_password = TRUE,
           updated_at           = NOW()
    WHERE  id = p_id;
$$;

CREATE OR REPLACE FUNCTION admin.fn_change_admin_password(
    p_id              BIGINT,
    p_hashed_password TEXT
) RETURNS void LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password         = p_hashed_password,
           must_change_password    = FALSE,
           invite_token            = NULL,
           invite_token_expires_at = NULL,
           updated_at              = NOW()
    WHERE  id = p_id;
$$;

CREATE OR REPLACE FUNCTION admin.fn_deactivate_admin_user(p_id BIGINT)
RETURNS void LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    is_active  = FALSE,
           updated_at = NOW()
    WHERE  id = p_id;
$$;

CREATE OR REPLACE FUNCTION admin.fn_touch_admin_login(p_id BIGINT)
RETURNS void LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    last_login_at = NOW()
    WHERE  id = p_id;
$$;

-- ── toolkit ──────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION toolkit.fn_list_common_fields(
    p_search    TEXT    DEFAULT NULL,
    p_is_active BOOLEAN DEFAULT NULL,
    p_role      TEXT    DEFAULT NULL,
    p_limit     INT     DEFAULT 100,
    p_offset    INT     DEFAULT 0
) RETURNS SETOF toolkit.v_common_fields LANGUAGE sql STABLE AS $$
    SELECT * FROM toolkit.v_common_fields
    WHERE  (p_is_active IS NULL OR is_active  = p_is_active)
      AND  (p_role      IS NULL OR field_role = p_role)
      AND  (p_search    IS NULL
            OR field_name ILIKE '%' || p_search || '%'
            OR code       ILIKE '%' || p_search || '%')
    ORDER  BY sort_order, code
    LIMIT  p_limit OFFSET p_offset;
$$;

CREATE OR REPLACE FUNCTION toolkit.fn_get_common_fields(p_id BIGINT)
RETURNS SETOF toolkit.v_common_fields LANGUAGE sql STABLE AS $$
    SELECT * FROM toolkit.v_common_fields WHERE id = p_id;
$$;

-- toolkit.fn_register_table() — bootstrap helper.
-- Introspects information_schema for an existing physical table and
-- inserts/updates toolkit.api_objects + toolkit.api_fields accordingly.
-- Safe to call multiple times (uses ON CONFLICT DO UPDATE).
CREATE OR REPLACE FUNCTION toolkit.fn_register_table(
    p_schema  TEXT,
    p_table   TEXT,
    p_name_en TEXT  DEFAULT NULL,
    p_actions JSONB DEFAULT '{"list":{"impl":"generic"},"get":{"impl":"generic"},"insert":{"impl":"generic"},"update":{"impl":"generic"},"delete":{"impl":"generic"}}',
    p_policy  JSONB DEFAULT '{"roles":{"list":["*"],"get":["*"],"insert":["admin","writer"],"update":["admin","writer"],"delete":["admin"]},"default_limit":25,"max_limit":200,"max_affected":500}'
)
RETURNS TEXT
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_code       TEXT   := p_schema || '.' || p_table;
    v_name_en    TEXT   := COALESCE(p_name_en, initcap(replace(p_table, '_', ' ')));
    v_obj_id     BIGINT;
    v_field_code TEXT;
    v_sort       INT    := 0;
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
            -- Preserve a customised slug; only fill when still NULL
            api_slug    = COALESCE(toolkit.api_objects.api_slug, EXCLUDED.api_slug),
            is_active   = TRUE;

    SELECT id INTO v_obj_id FROM toolkit.api_objects WHERE code = v_code;

    -- Deactivate fields that no longer exist in the physical table
    UPDATE toolkit.api_fields af
    SET    is_active = FALSE
    WHERE  af.object_code = v_code
      AND  af.field_name NOT IN (
               SELECT column_name
               FROM   information_schema.columns
               WHERE  table_schema = p_schema AND table_name = p_table
           );

    -- Insert / update fields from information_schema
    v_sort := 0;
    FOR col IN
        SELECT column_name, data_type, ordinal_position,
               column_default, is_nullable
        FROM   information_schema.columns
        WHERE  table_schema = p_schema AND table_name = p_table
        ORDER  BY ordinal_position
    LOOP
        v_sort       := v_sort + 10;
        v_field_code := v_code || '.' || col.column_name;

        INSERT INTO toolkit.api_fields
            (code, object_code, field_name, field_type, "name",
             is_readonly, editable_on_update, in_list, in_form,
             sort_order, is_active)
        VALUES (
            v_field_code,
            v_code,
            col.column_name,
            CASE
                WHEN col.column_name IN ('inserted_at','modified_at')
                    THEN 'timestamp'
                WHEN col.column_name IN ('inserted_by','modified_by')
                    THEN 'text'
                WHEN col.data_type IN (
                    'integer','bigint','smallint','numeric','real','double precision')
                    THEN 'number'
                WHEN col.data_type IN ('boolean')
                    THEN 'boolean'
                WHEN col.data_type IN ('date')
                    THEN 'date'
                WHEN col.data_type IN (
                    'timestamp with time zone','timestamp without time zone')
                    THEN 'timestamp'
                WHEN col.data_type IN ('jsonb','json')
                    THEN 'json'
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
$fn$;




















































































































































































































































































































































-- ══ USER TABLES (auto-generated — do not edit below) ══

-- ── company ─────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS company;

-- company_languages
CREATE TABLE IF NOT EXISTS company.company_languages (
    id BIGSERIAL PRIMARY KEY,
    code text NOT NULL,
    sort_order bigint NOT NULL DEFAULT nextval('company.company_languages_sort_order_seq'::regclass),
    is_active boolean NOT NULL DEFAULT false,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    iso3 text NOT NULL,
    iso2 text NOT NULL,
    name text DEFAULT ''::text,
    flag_icon text DEFAULT ''::text,
    UNIQUE (code)
);

CREATE OR REPLACE FUNCTION company.fn_list_company_languages(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'company', 'toolkit'
AS $function$
    select toolkit.fn_list('company_languages', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- ── pim ─────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS pim;

-- asset_links
CREATE TABLE IF NOT EXISTS pim.asset_links (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    asset_code text NOT NULL,
    owner_type text NOT NULL DEFAULT 'product'::text,
    owner_code text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    role text NOT NULL DEFAULT 'gallery'::text,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_asset_links CASCADE;
CREATE VIEW pim.v_asset_links AS
 SELECT t.id,
    t.code,
    t.asset_code,
    t.owner_type,
    t.owner_code,
    t.sort_order,
    t.is_active,
    t.role,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((ref_1.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_1.name ->> 'en'::text)) AS asset_code_name,
        CASE
            WHEN t.is_active THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_active_label
   FROM ((asset_links t
     LEFT JOIN assets ref_1 ON ((ref_1.code = t.asset_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'asset_links'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_asset_links(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('asset_links', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- assets
CREATE TABLE IF NOT EXISTS pim.assets (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    asset_type text NOT NULL DEFAULT 'image'::text,
    sort_order integer NOT NULL DEFAULT 0,
    storage_key text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    mime_type text,
    file_bytes integer NOT NULL DEFAULT 0,
    inserted_at timestamp with time zone,
    checksum text,
    inserted_by text,
    locale_code text,
    modified_at timestamp with time zone,
    modified_by text,
    renditions jsonb,
    UNIQUE (code),
    UNIQUE (storage_key)
);

DROP VIEW IF EXISTS pim.v_assets CASCADE;
CREATE VIEW pim.v_assets AS
 SELECT t.id,
    t.code,
    t.asset_type,
    t.sort_order,
    t.storage_key,
    t.is_active,
    t.mime_type,
    t.file_bytes,
    t.inserted_at,
    t.checksum,
    t.inserted_by,
    t.locale_code,
    t.modified_at,
    t.modified_by,
    t.renditions,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    opt_2.label AS asset_type_label,
        CASE
            WHEN t.is_active THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_active_label
   FROM ((assets t
     LEFT JOIN toolkit_field_options opt_2 ON (((opt_2.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'assets'::text) AND (tf.code = 'asset_type'::text))
         LIMIT 1)) AND (opt_2.code = t.asset_type))))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'assets'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_3 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_assets(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('assets', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- attribute_groups
CREATE TABLE IF NOT EXISTS pim.attribute_groups (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_attribute_groups CASCADE;
CREATE VIEW pim.v_attribute_groups AS
 SELECT t.id,
    t.code,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
        CASE
            WHEN t.is_active THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_active_label
   FROM (attribute_groups t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attribute_groups'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_attribute_groups(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('attribute_groups', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- attributes
CREATE TABLE IF NOT EXISTS pim.attributes (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    description jsonb,
    attr_type text NOT NULL DEFAULT 'text'::text,
    sort_order integer NOT NULL DEFAULT 0,
    group_code text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    unit_family text,
    inserted_by text,
    unit_code text,
    is_localizable boolean NOT NULL DEFAULT false,
    modified_at timestamp with time zone,
    modified_by text,
    is_searchable boolean NOT NULL DEFAULT false,
    is_facet boolean NOT NULL DEFAULT false,
    is_sortable boolean NOT NULL DEFAULT false,
    options jsonb,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_attributes CASCADE;
CREATE VIEW pim.v_attributes AS
 SELECT t.id,
    t.code,
    t.attr_type,
    t.sort_order,
    t.group_code,
    t.is_active,
    t.inserted_at,
    t.unit_family,
    t.inserted_by,
    t.unit_code,
    t.is_localizable,
    t.modified_at,
    t.is_searchable,
    t.modified_by,
    t.is_facet,
    t.is_sortable,
    t.options,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    COALESCE((t.description ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.description ->> 'en'::text)) AS description,
    opt_3.label AS attr_type_label,
    COALESCE((ref_4.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_4.name ->> 'en'::text)) AS group_code_name,
        CASE
            WHEN t.is_active THEN lbl_5.true_lbl
            ELSE lbl_5.false_lbl
        END AS is_active_label,
    COALESCE((ref_6.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_6.name ->> 'en'::text)) AS unit_family_name,
    COALESCE((ref_7.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_7.name ->> 'en'::text)) AS unit_code_name,
        CASE
            WHEN t.is_localizable THEN lbl_8.true_lbl
            ELSE lbl_8.false_lbl
        END AS is_localizable_label,
        CASE
            WHEN t.is_searchable THEN lbl_9.true_lbl
            ELSE lbl_9.false_lbl
        END AS is_searchable_label,
        CASE
            WHEN t.is_facet THEN lbl_10.true_lbl
            ELSE lbl_10.false_lbl
        END AS is_facet_label,
        CASE
            WHEN t.is_sortable THEN lbl_11.true_lbl
            ELSE lbl_11.false_lbl
        END AS is_sortable_label
   FROM (((((((((attributes t
     LEFT JOIN toolkit_field_options opt_3 ON (((opt_3.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'attr_type'::text))
         LIMIT 1)) AND (opt_3.code = t.attr_type))))
     LEFT JOIN attribute_groups ref_4 ON ((ref_4.code = t.group_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_5 ON (true))
     LEFT JOIN unit_families ref_6 ON ((ref_6.code = t.unit_family)))
     LEFT JOIN units ref_7 ON ((ref_7.code = t.unit_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'is_localizable'::text))
         LIMIT 1) lbl_8 ON (true))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'is_searchable'::text))
         LIMIT 1) lbl_9 ON (true))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'is_facet'::text))
         LIMIT 1) lbl_10 ON (true))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'attributes'::text) AND (tf.code = 'is_sortable'::text))
         LIMIT 1) lbl_11 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_attributes(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('attributes', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- brands
CREATE TABLE IF NOT EXISTS pim.brands (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    brand_type text NOT NULL DEFAULT 'own_brand'::text,
    description jsonb,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    quality_tier text DEFAULT 'economy'::text,
    inserted_at timestamp with time zone,
    website_url text,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_brands CASCADE;
CREATE VIEW pim.v_brands AS
 SELECT t.id,
    t.code,
    t.name,
    t.brand_type,
    t.sort_order,
    t.is_active,
    t.quality_tier,
    t.inserted_at,
    t.website_url,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    opt_1.label AS brand_type_label,
    COALESCE((t.description ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.description ->> 'en'::text)) AS description,
        CASE
            WHEN t.is_active THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_active_label,
    opt_4.label AS quality_tier_label
   FROM (((brands t
     LEFT JOIN toolkit_field_options opt_1 ON (((opt_1.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'brands'::text) AND (tf.code = 'brand_type'::text))
         LIMIT 1)) AND (opt_1.code = t.brand_type))))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'brands'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_3 ON (true))
     LEFT JOIN toolkit_field_options opt_4 ON (((opt_4.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'brands'::text) AND (tf.code = 'quality_tier'::text))
         LIMIT 1)) AND (opt_4.code = t.quality_tier))))
  ORDER BY t.sort_order, t.code;

CREATE OR REPLACE FUNCTION pim.fn_list_brands(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('brands', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- categories
CREATE TABLE IF NOT EXISTS pim.categories (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    tree_code text NOT NULL DEFAULT 'internal'::text,
    parent_code text,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    path text,
    description jsonb,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    has_children boolean DEFAULT false,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_categories CASCADE;
CREATE VIEW pim.v_categories AS
 SELECT t.id,
    t.code,
    t.tree_code,
    t.parent_code,
    t.sort_order,
    t.is_active,
    t.path,
    t.inserted_at,
    t.has_children,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    lbl_2.label AS is_active_label,
    COALESCE((t.description ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.description ->> 'en'::text)) AS description,
    lbl_4.label AS has_children_label
   FROM ((categories t
     LEFT JOIN LATERAL ( SELECT
                CASE
                    WHEN t.is_active THEN COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text)
                    ELSE COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text)
                END AS label
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'categories'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true))
     LEFT JOIN LATERAL ( SELECT
                CASE
                    WHEN t.has_children THEN COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text)
                    ELSE COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text)
                END AS label
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'categories'::text) AND (tf.code = 'has_children'::text))
         LIMIT 1) lbl_4 ON (true))
  ORDER BY t.sort_order, t.code;

CREATE OR REPLACE FUNCTION pim.fn_list_categories(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('categories', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- countries
CREATE TABLE IF NOT EXISTS pim.countries (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    iso_code text NOT NULL,
    locale_code text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    region_code text,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code),
    UNIQUE (iso_code)
);

DROP VIEW IF EXISTS pim.v_countries CASCADE;
CREATE VIEW pim.v_countries AS
 SELECT t.id,
    t.code,
    t.iso_code,
    t.locale_code,
    t.sort_order,
    t.is_active,
    t.region_code,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    COALESCE((ref_2.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_2.name ->> 'en'::text)) AS locale_code_name,
        CASE
            WHEN t.is_active THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_active_label,
    COALESCE((ref_4.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_4.name ->> 'en'::text)) AS region_code_name
   FROM (((countries t
     LEFT JOIN locales ref_2 ON ((ref_2.code = t.locale_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'countries'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_3 ON (true))
     LEFT JOIN regions ref_4 ON ((ref_4.code = t.region_code)));

CREATE OR REPLACE FUNCTION pim.fn_list_countries(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('countries', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- cross_references
CREATE TABLE IF NOT EXISTS pim.cross_references (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    product_code text NOT NULL,
    brand_code text NOT NULL,
    part_number text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    part_number_norm text NOT NULL,
    cross_type text NOT NULL DEFAULT 'oem'::text,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_cross_references CASCADE;
CREATE VIEW pim.v_cross_references AS
 SELECT t.id,
    t.code,
    t.product_code,
    t.brand_code,
    t.part_number,
    t.sort_order,
    t.is_active,
    t.part_number_norm,
    t.cross_type,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
        CASE
            WHEN t.is_active THEN lbl_1.true_lbl
            ELSE lbl_1.false_lbl
        END AS is_active_label,
    opt_2.label AS cross_type_label
   FROM ((cross_references t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'cross_references'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_1 ON (true))
     LEFT JOIN toolkit_field_options opt_2 ON (((opt_2.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'cross_references'::text) AND (tf.code = 'cross_type'::text))
         LIMIT 1)) AND (opt_2.code = t.cross_type))));

CREATE OR REPLACE FUNCTION pim.fn_list_cross_references(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('cross_references', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- customers
CREATE TABLE IF NOT EXISTS pim.customers (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name text NOT NULL,
    customer_type text NOT NULL DEFAULT 'distributor'::text,
    sort_order integer NOT NULL DEFAULT 0,
    tax_id text,
    country_code text,
    is_active boolean NOT NULL DEFAULT true,
    email text,
    inserted_at timestamp with time zone,
    inserted_by text,
    phone text,
    modified_at timestamp with time zone,
    website_url text,
    credit_limit numeric(15,4),
    modified_by text,
    payment_terms text,
    notes text,
    UNIQUE (code),
    UNIQUE (tax_id)
);

DROP VIEW IF EXISTS pim.v_customers CASCADE;
CREATE VIEW pim.v_customers AS
 SELECT t.id,
    t.code,
    t.name,
    t.customer_type,
    t.sort_order,
    t.tax_id,
    t.country_code,
    t.is_active,
    t.email,
    t.inserted_at,
    t.inserted_by,
    t.phone,
    t.modified_at,
    t.website_url,
    t.credit_limit,
    t.modified_by,
    t.payment_terms,
    t.notes,
    opt_1.label AS customer_type_label,
        CASE
            WHEN t.is_active THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_active_label
   FROM ((customers t
     LEFT JOIN toolkit_field_options opt_1 ON (((opt_1.field_id = ( SELECT tf.id
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'customers'::text) AND (tf.code = 'customer_type'::text))
         LIMIT 1)) AND (opt_1.code = t.customer_type))))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'customers'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_get_customers(p_id jsonb, p_lang text DEFAULT 'en'::text)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select pim.fn_list_customers(p_id, NULL::jsonb, p_lang, 1, 0, false) -> 'rows' -> 0;
$function$;

CREATE OR REPLACE FUNCTION pim.fn_list_customers(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('customers', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- families
CREATE TABLE IF NOT EXISTS pim.families (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    description jsonb,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_families CASCADE;
CREATE VIEW pim.v_families AS
 SELECT t.id,
    t.code,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    COALESCE((t.description ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.description ->> 'en'::text)) AS description,
        CASE
            WHEN t.is_active THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_active_label
   FROM (families t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'families'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_3 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_families(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('families', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- family_attributes
CREATE TABLE IF NOT EXISTS pim.family_attributes (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    family_code text NOT NULL,
    attribute_code text NOT NULL,
    is_required boolean NOT NULL DEFAULT false,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_family_attributes CASCADE;
CREATE VIEW pim.v_family_attributes AS
 SELECT t.id,
    t.code,
    t.family_code,
    t.attribute_code,
    t.is_required,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((ref_1.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_1.name ->> 'en'::text)) AS family_code_name,
    COALESCE((ref_2.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_2.name ->> 'en'::text)) AS attribute_code_name,
        CASE
            WHEN t.is_required THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_required_label,
        CASE
            WHEN t.is_active THEN lbl_4.true_lbl
            ELSE lbl_4.false_lbl
        END AS is_active_label
   FROM ((((family_attributes t
     LEFT JOIN families ref_1 ON ((ref_1.code = t.family_code)))
     LEFT JOIN attributes ref_2 ON ((ref_2.code = t.attribute_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'family_attributes'::text) AND (tf.code = 'is_required'::text))
         LIMIT 1) lbl_3 ON (true))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'family_attributes'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_4 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_family_attributes(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('family_attributes', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- locales
CREATE TABLE IF NOT EXISTS pim.locales (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    fallback_code text,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_locales CASCADE;
CREATE VIEW pim.v_locales AS
 SELECT t.id,
    t.code,
    t.is_default,
    t.fallback_code,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
        CASE
            WHEN t.is_default THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_default_label,
    ref_3.code AS fallback_code_code,
        CASE
            WHEN t.is_active THEN lbl_4.true_lbl
            ELSE lbl_4.false_lbl
        END AS is_active_label
   FROM (((locales t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'locales'::text) AND (tf.code = 'is_default'::text))
         LIMIT 1) lbl_2 ON (true))
     LEFT JOIN company_languages ref_3 ON ((ref_3.code = t.fallback_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'locales'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_4 ON (true))
  ORDER BY t.sort_order, t.code;

CREATE OR REPLACE FUNCTION pim.fn_list_locales(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('locales', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- products
CREATE TABLE IF NOT EXISTS pim.products (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name text NOT NULL,
    family_code text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    values jsonb NOT NULL DEFAULT '{}'::jsonb,
    categories jsonb,
    is_active boolean NOT NULL DEFAULT true,
    hs_code text,
    inserted_at timestamp with time zone,
    inserted_by text,
    superseded_by_code text,
    launched_at date,
    modified_at timestamp with time zone,
    discontinued_at date,
    modified_by text,
    tags jsonb DEFAULT '[]'::jsonb,
    categories_code text[] DEFAULT '{}'::text[],
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_products CASCADE;
CREATE VIEW pim.v_products AS
 SELECT t.id,
    t.code,
    t.name,
    t.family_code,
    t.sort_order,
    t."values",
    t.categories,
    t.is_active,
    t.hs_code,
    t.inserted_at,
    t.inserted_by,
    t.superseded_by_code,
    t.launched_at,
    t.modified_at,
    t.discontinued_at,
    t.modified_by,
    t.tags,
    t.categories_code,
        CASE
            WHEN t.is_active THEN lbl_1.true_lbl
            ELSE lbl_1.false_lbl
        END AS is_active_label
   FROM (products t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'products'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_1 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_get_products(p_id jsonb, p_lang text DEFAULT 'en'::text)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select pim.fn_list_products(p_id, NULL::jsonb, p_lang, 1, 0, false) -> 'rows' -> 0;
$function$;

CREATE OR REPLACE FUNCTION pim.fn_list_products(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('products', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- product_variants
CREATE TABLE IF NOT EXISTS pim.product_variants (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    product_code text NOT NULL,
    brand_code text NOT NULL,
    item_id text,
    sort_order integer NOT NULL DEFAULT 0,
    ean text,
    is_active boolean NOT NULL DEFAULT true,
    axis jsonb,
    inserted_at timestamp with time zone,
    inserted_by text,
    values jsonb NOT NULL DEFAULT '{}'::jsonb,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code),
    UNIQUE (item_id)
);

DROP VIEW IF EXISTS pim.v_product_variants CASCADE;
CREATE VIEW pim.v_product_variants AS
 SELECT t.id,
    t.code,
    t.product_code,
    t.brand_code,
    t.item_id,
    t.sort_order,
    t.ean,
    t.is_active,
    t.axis,
    t.inserted_at,
    t.inserted_by,
    t."values",
    t.modified_at,
    t.modified_by,
        CASE
            WHEN t.is_active THEN lbl_1.true_lbl
            ELSE lbl_1.false_lbl
        END AS is_active_label
   FROM (product_variants t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'product_variants'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_1 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_get_product_variants(p_id jsonb, p_lang text DEFAULT 'en'::text)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select pim.fn_list_product_variants(p_id, NULL::jsonb, p_lang, 1, 0, false) -> 'rows' -> 0;
$function$;

CREATE OR REPLACE FUNCTION pim.fn_list_product_variants(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('product_variants', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- regions
CREATE TABLE IF NOT EXISTS pim.regions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_regions CASCADE;
CREATE VIEW pim.v_regions AS
 SELECT t.id,
    t.code,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
        CASE
            WHEN t.is_active THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_active_label
   FROM (regions t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'regions'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_get_regions(p_id jsonb, p_lang text DEFAULT 'en'::text)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select pim.fn_list_regions(p_id, NULL::jsonb, p_lang, 1, 0, false) -> 'rows' -> 0;
$function$;

CREATE OR REPLACE FUNCTION pim.fn_list_regions(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('regions', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- unit_conversions
CREATE TABLE IF NOT EXISTS pim.unit_conversions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    from_unit_code text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    to_unit_code text NOT NULL,
    factor numeric(10,4) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    offset numeric(10,4) NOT NULL DEFAULT 0,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_unit_conversions CASCADE;
CREATE VIEW pim.v_unit_conversions AS
 SELECT t.id,
    t.code,
    t.from_unit_code,
    t.sort_order,
    t.to_unit_code,
    t.factor,
    t.is_active,
    t.inserted_at,
    t."offset",
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    COALESCE((ref_2.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_2.name ->> 'en'::text)) AS from_unit_code_name,
    COALESCE((ref_3.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_3.name ->> 'en'::text)) AS to_unit_code_name,
        CASE
            WHEN t.is_active THEN lbl_4.true_lbl
            ELSE lbl_4.false_lbl
        END AS is_active_label
   FROM (((unit_conversions t
     LEFT JOIN units ref_2 ON ((ref_2.code = t.from_unit_code)))
     LEFT JOIN units ref_3 ON ((ref_3.code = t.to_unit_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'unit_conversions'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_4 ON (true))
  ORDER BY t.sort_order, t.code;

CREATE OR REPLACE FUNCTION pim.fn_list_unit_conversions(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('unit_conversions', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- unit_families
CREATE TABLE IF NOT EXISTS pim.unit_families (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_unit_families CASCADE;
CREATE VIEW pim.v_unit_families AS
 SELECT t.id,
    t.code,
    t.sort_order,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
        CASE
            WHEN t.is_active THEN lbl_2.true_lbl
            ELSE lbl_2.false_lbl
        END AS is_active_label
   FROM (unit_families t
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'unit_families'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_2 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_unit_families(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('unit_families', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- units
CREATE TABLE IF NOT EXISTS pim.units (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name jsonb NOT NULL,
    symbol text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    unit_family_code text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone,
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS pim.v_units CASCADE;
CREATE VIEW pim.v_units AS
 SELECT t.id,
    t.code,
    t.symbol,
    t.sort_order,
    t.unit_family_code,
    t.is_active,
    t.inserted_at,
    t.inserted_by,
    t.modified_at,
    t.modified_by,
    COALESCE((t.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (t.name ->> 'en'::text)) AS name,
    COALESCE((ref_2.name ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), (ref_2.name ->> 'en'::text)) AS unit_family_code_name,
        CASE
            WHEN t.is_active THEN lbl_3.true_lbl
            ELSE lbl_3.false_lbl
        END AS is_active_label
   FROM ((units t
     LEFT JOIN unit_families ref_2 ON ((ref_2.code = t.unit_family_code)))
     LEFT JOIN LATERAL ( SELECT COALESCE(((tf.config -> 'true_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'true_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'true_label'::text), 'Yes'::text) AS true_lbl,
            COALESCE(((tf.config -> 'false_label_i18n'::text) ->> NULLIF(current_setting('app.lang'::text, true), ''::text)), ((tf.config -> 'false_label_i18n'::text) ->> 'en'::text), (tf.config ->> 'false_label'::text), 'No'::text) AS false_lbl
           FROM (toolkit_fields tf
             JOIN toolkit_tables tt ON ((tt.id = tf.table_id)))
          WHERE ((tt.code = 'units'::text) AND (tf.code = 'is_active'::text))
         LIMIT 1) lbl_3 ON (true));

CREATE OR REPLACE FUNCTION pim.fn_list_units(p_filters jsonb DEFAULT '{}'::jsonb, p_sort jsonb DEFAULT NULL::jsonb, p_lang text DEFAULT 'en'::text, p_limit integer DEFAULT 25, p_offset integer DEFAULT 0, p_with_total boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE sql
 SET search_path TO 'pg_catalog', 'pim', 'toolkit'
AS $function$
    select toolkit.fn_list('units', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);
$function$;

-- ── toolkit ─────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS toolkit;

-- Common Fields
CREATE TABLE IF NOT EXISTS toolkit.common_fields (
    id BIGSERIAL PRIMARY KEY,
    code text NOT NULL,
    field_name text NOT NULL,
    data_type text NOT NULL,
    field_role text NOT NULL DEFAULT 'user'::text,
    show_translation boolean NOT NULL DEFAULT true,
    is_active boolean NOT NULL DEFAULT true,
    inserted_at timestamp with time zone NOT NULL DEFAULT now(),
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    sort_order bigint DEFAULT nextval('common_fields_sort_order_seq'::regclass),
    default_value text,
    true_label text,
    false_label text,
    field_type text NOT NULL DEFAULT 'text'::text,
    UNIQUE (code)
);

DROP VIEW IF EXISTS toolkit.v_common_fields CASCADE;
CREATE VIEW toolkit.v_common_fields AS
 SELECT id,
    code,
    field_name,
    data_type,
    field_role,
    show_translation,
    is_active,
    inserted_at,
    inserted_by,
    modified_at,
    modified_by,
    sort_order,
    default_value,
    true_label,
    false_label,
    field_type,
    ( SELECT common_fields_translation.label
           FROM common_fields_translation
          WHERE ((common_fields_translation.common_field_id = cf.id) AND (common_fields_translation.lang = 'en'::text))
         LIMIT 1) AS translated_label,
    ( SELECT common_fields_translation.description
           FROM common_fields_translation
          WHERE ((common_fields_translation.common_field_id = cf.id) AND (common_fields_translation.lang = 'en'::text))
         LIMIT 1) AS translated_description
   FROM common_fields cf
  ORDER BY sort_order, code;

CREATE OR REPLACE FUNCTION toolkit.fn_get_common_fields(p_id bigint)
 RETURNS SETOF v_common_fields
 LANGUAGE sql
 STABLE
AS $function$
            SELECT * FROM toolkit.v_common_fields WHERE id = p_id;
        $function$;

CREATE OR REPLACE FUNCTION toolkit.fn_list_common_fields(p_search text DEFAULT NULL::text, p_is_active boolean DEFAULT NULL::boolean, p_role text DEFAULT NULL::text, p_limit integer DEFAULT 100, p_offset integer DEFAULT 0)
 RETURNS SETOF v_common_fields
 LANGUAGE sql
 STABLE
AS $function$
            SELECT * FROM toolkit.v_common_fields
            WHERE (p_is_active IS NULL OR is_active = p_is_active)
              AND (p_role IS NULL OR field_role = p_role)
              AND (p_search IS NULL
                   OR field_name ILIKE '%' || p_search || '%'
                   OR code ILIKE '%' || p_search || '%')
            ORDER BY sort_order, code
            LIMIT p_limit OFFSET p_offset;
        $function$;

-- Common Fields Translation
CREATE TABLE IF NOT EXISTS toolkit.common_fields_translation (
    id BIGSERIAL PRIMARY KEY,
    common_field_id bigint NOT NULL,
    lang_code text NOT NULL,
    label text NOT NULL,
    inserted_at timestamp with time zone NOT NULL DEFAULT now(),
    inserted_by text,
    modified_at timestamp with time zone,
    modified_by text,
    lang text NOT NULL DEFAULT 'en'::text,
    description text,
    true_label text,
    false_label text,
    UNIQUE (common_field_id, lang_code)
);
