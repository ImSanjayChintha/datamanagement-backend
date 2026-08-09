-- =============================================================================
-- Admin schema setup — complete, safe to run multiple times.
-- Run this in psql or pgAdmin after the toolkit catalog tables exist.
-- =============================================================================


-- ── 1. Ensure admin schema exists ────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS admin;
COMMENT ON SCHEMA admin IS 'Platform management: admin users, SMTP config, email templates, API keys';


-- ── 2. Create admin_users table (if it does not exist yet) ───────────────────

CREATE TABLE IF NOT EXISTS admin.admin_users (
    id                      BIGSERIAL PRIMARY KEY,
    email                   TEXT NOT NULL UNIQUE,
    full_name               TEXT,
    role                    TEXT NOT NULL DEFAULT 'admin'
                                CHECK (role IN ('admin', 'local_admin', 'writer', 'reader')),
    hashed_password         TEXT NOT NULL,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password    BOOLEAN NOT NULL DEFAULT FALSE,
    invite_token            TEXT,
    invite_token_expires_at TIMESTAMPTZ,
    last_login_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_users_email
    ON admin.admin_users (email);
CREATE INDEX IF NOT EXISTS idx_admin_users_is_active
    ON admin.admin_users (is_active);


-- ── 3. Move admin_users from toolkit schema if it landed there ────────────────
--   (happens when public was renamed to toolkit before the table was moved)

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'toolkit' AND table_name = 'admin_users'
    ) THEN
        EXECUTE 'ALTER TABLE toolkit.admin_users SET SCHEMA admin';
        RAISE NOTICE 'admin_users moved: toolkit → admin';
    END IF;

    -- Move owned sequence if still in toolkit
    IF EXISTS (
        SELECT 1 FROM information_schema.sequences
        WHERE sequence_schema = 'toolkit' AND sequence_name = 'admin_users_id_seq'
    ) THEN
        EXECUTE 'ALTER SEQUENCE toolkit.admin_users_id_seq SET SCHEMA admin';
        EXECUTE $q$
            ALTER TABLE admin.admin_users
                ALTER COLUMN id SET DEFAULT nextval('admin.admin_users_id_seq'::regclass)
        $q$;
    END IF;
END $$;


-- ── 4. v_admin_users — read view (lives in toolkit, default search_path) ─────
--   Explicitly drop from admin schema first in case it was mistakenly created there.

DROP VIEW IF EXISTS admin.v_admin_users CASCADE;
DROP VIEW IF EXISTS v_admin_users CASCADE;

CREATE OR REPLACE VIEW v_admin_users AS
SELECT
    id,
    email,
    full_name,
    role,
    hashed_password,
    is_active,
    must_change_password,
    invite_token,
    invite_token_expires_at,
    last_login_at,
    created_at,
    updated_at
FROM  admin.admin_users
ORDER BY created_at DESC;

COMMENT ON VIEW v_admin_users IS
    'Read view for admin.admin_users — single source for all admin user SELECTs.';


-- ── 5. fn_list_admin_users — paginated list ───────────────────────────────────
--   Called as: fn_list_admin_users($1, $2, $3, $4)
--   Params:    p_role, p_limit, p_offset, p_search

CREATE OR REPLACE FUNCTION fn_list_admin_users(
    p_role    TEXT    DEFAULT NULL,
    p_limit   INT     DEFAULT 50,
    p_offset  INT     DEFAULT 0,
    p_search  TEXT    DEFAULT NULL
)
RETURNS SETOF v_admin_users
LANGUAGE sql STABLE AS $$
    SELECT *
    FROM   v_admin_users
    WHERE  (p_role   IS NULL OR role      = p_role)
      AND  (p_search IS NULL
            OR email     ILIKE '%' || p_search || '%'
            OR full_name ILIKE '%' || p_search || '%')
    ORDER  BY created_at DESC
    LIMIT  p_limit
    OFFSET p_offset;
$$;


-- ── 6. fn_get_admin_users — single row by id ─────────────────────────────────

CREATE OR REPLACE FUNCTION fn_get_admin_users(p_id BIGINT)
RETURNS SETOF v_admin_users
LANGUAGE sql STABLE AS $$
    SELECT * FROM v_admin_users WHERE id = p_id;
$$;


-- ── 7. fn_create_admin_user — invite INSERT ───────────────────────────────────

CREATE OR REPLACE FUNCTION fn_create_admin_user(
    p_email           TEXT,
    p_full_name       TEXT,
    p_hashed_password TEXT,
    p_role            TEXT,
    p_invite_token    TEXT
)
RETURNS SETOF v_admin_users
LANGUAGE sql VOLATILE AS $$
    INSERT INTO admin.admin_users
        (email, full_name, hashed_password, role,
         must_change_password, invite_token, invite_token_expires_at, is_active)
    VALUES (p_email, p_full_name, p_hashed_password, p_role,
            TRUE, p_invite_token, NOW() + INTERVAL '7 days', TRUE)
    RETURNING id, email, full_name, role, hashed_password, is_active,
              must_change_password, invite_token, invite_token_expires_at,
              last_login_at, created_at, updated_at;
$$;


-- ── 8. fn_resend_admin_invite — refresh token + temp password ─────────────────

CREATE OR REPLACE FUNCTION fn_resend_admin_invite(
    p_id              BIGINT,
    p_hashed_password TEXT,
    p_invite_token    TEXT
)
RETURNS void
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password         = p_hashed_password,
           invite_token            = p_invite_token,
           invite_token_expires_at = NOW() + INTERVAL '7 days',
           updated_at              = NOW()
    WHERE  id = p_id;
$$;


-- ── 9. fn_update_admin_user — partial update (NULL = keep current) ────────────

CREATE OR REPLACE FUNCTION fn_update_admin_user(
    p_id        BIGINT,
    p_full_name TEXT    DEFAULT NULL,
    p_is_active BOOLEAN DEFAULT NULL,
    p_role      TEXT    DEFAULT NULL
)
RETURNS SETOF v_admin_users
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    full_name  = COALESCE(p_full_name, full_name),
           is_active  = COALESCE(p_is_active, is_active),
           role       = COALESCE(p_role,      role),
           updated_at = NOW()
    WHERE  id = p_id
    RETURNING id, email, full_name, role, hashed_password, is_active,
              must_change_password, invite_token, invite_token_expires_at,
              last_login_at, created_at, updated_at;
$$;


-- ── 10. fn_reset_admin_password — force-sets new hash, flags must-change ──────

CREATE OR REPLACE FUNCTION fn_reset_admin_password(
    p_id              BIGINT,
    p_hashed_password TEXT
)
RETURNS void
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password      = p_hashed_password,
           must_change_password = TRUE,
           updated_at           = NOW()
    WHERE  id = p_id;
$$;


-- ── 11. fn_change_admin_password — self-service, clears force-change flag ─────

CREATE OR REPLACE FUNCTION fn_change_admin_password(
    p_id              BIGINT,
    p_hashed_password TEXT
)
RETURNS void
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    hashed_password         = p_hashed_password,
           must_change_password    = FALSE,
           invite_token            = NULL,
           invite_token_expires_at = NULL,
           updated_at              = NOW()
    WHERE  id = p_id;
$$;


-- ── 12. fn_deactivate_admin_user ──────────────────────────────────────────────

CREATE OR REPLACE FUNCTION fn_deactivate_admin_user(p_id BIGINT)
RETURNS void
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    is_active  = FALSE,
           updated_at = NOW()
    WHERE  id = p_id;
$$;


-- ── 13. fn_touch_admin_login — stamps last_login_at on login ─────────────────

CREATE OR REPLACE FUNCTION fn_touch_admin_login(p_id BIGINT)
RETURNS void
LANGUAGE sql VOLATILE AS $$
    UPDATE admin.admin_users
    SET    last_login_at = NOW()
    WHERE  id = p_id;
$$;


-- (admin user seeding is done by scripts/reset_admin.py which generates a valid bcrypt hash)
