"""
Module: api_bridge.resources.db.setup
Purpose: Ensure api_gateway.api_resources exists on every startup.
         Migrates data from apib.api_resources if the old table is still present.
"""
import asyncpg


async def setup_api_bridge(conn: asyncpg.Connection) -> None:
    print("[setup] ── api_bridge schema setup ──────────────────────────────────")

    # api_gateway schema is created by the gateway setup — ensure it exists here too
    await conn.execute("CREATE SCHEMA IF NOT EXISTS api_gateway")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS api_gateway.api_resources (
            id              BIGSERIAL PRIMARY KEY,
            code            TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT,
            base_url        TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL DEFAULT 30,
            auth_type       TEXT NOT NULL DEFAULT 'none'
                            CHECK (auth_type IN ('none', 'bearer', 'basic', 'api_key', 'oauth2')),
            auth_config     JSONB NOT NULL DEFAULT '{}',
            default_headers JSONB NOT NULL DEFAULT '{}',
            ssl_verify      BOOLEAN NOT NULL DEFAULT TRUE,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            inserted_by     TEXT,
            modified_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            modified_by     TEXT,
            CONSTRAINT uq_api_resources_code UNIQUE (code)
        )
    """)

    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_resources_is_active ON api_gateway.api_resources (is_active)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_resources_auth_type ON api_gateway.api_resources (auth_type)"
    )

    # ── Migrate data from old apib.api_resources if it still exists ───────────
    old_exists = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'apib' AND table_name = 'api_resources'
        )
    """)
    if old_exists:
        moved = await conn.fetchval("""
            WITH moved AS (
                INSERT INTO api_gateway.api_resources
                    (code, name, description, base_url, timeout_seconds,
                     auth_type, auth_config, default_headers, ssl_verify, is_active,
                     inserted_at, inserted_by, modified_at, modified_by)
                SELECT
                    code, name, description, base_url, timeout_seconds,
                    auth_type, auth_config, default_headers, ssl_verify, is_active,
                    inserted_at, inserted_by, modified_at, modified_by
                FROM apib.api_resources
                ON CONFLICT (code) DO NOTHING
                RETURNING 1
            )
            SELECT COUNT(*) FROM moved
        """)
        print(f"[setup] migrated {moved} row(s) from apib.api_resources → api_gateway.api_resources")
        await conn.execute(
            "ALTER TABLE apib.api_resources RENAME TO api_resources_migrated_bak"
        )
        print("[setup] apib.api_resources renamed to apib.api_resources_migrated_bak (backup)")

    print("[setup] api_gateway.api_resources table ✓")
    print("[setup] ── api_bridge schema ready ──────────────────────────────────")
