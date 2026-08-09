import asyncpg


VALID_TYPES = (
    'azure_blob', 'email', 's3_compatible', 'amazon_s3', 'filesystem',
    'rabbitmq', 'sftp', 'http_api',
)

_CHECK_CONSTRAINT = f"dest_type IN {str(VALID_TYPES)}"


async def setup_push_destinations(conn: asyncpg.Connection) -> None:
    print("[setup] ── push_destinations setup ──────────────────────────────────")

    await conn.execute("CREATE SCHEMA IF NOT EXISTS api_gateway")

    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS api_gateway.push_destinations (
            id          BIGSERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            dest_type   TEXT NOT NULL
                        CHECK ({_CHECK_CONSTRAINT}),
            config      JSONB NOT NULL DEFAULT '{{}}',
            secrets     JSONB NOT NULL DEFAULT '{{}}',
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            inserted_by TEXT,
            modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            modified_by TEXT,
            CONSTRAINT uq_push_destinations_name UNIQUE (name)
        )
    """)

    # Migrate the CHECK constraint when new dest_types are added.
    # Drop all unnamed check constraints on dest_type and recreate.
    chk_names = await conn.fetch("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'api_gateway.push_destinations'::regclass
          AND contype  = 'c'
          AND pg_get_constraintdef(oid) LIKE '%dest_type%'
    """)
    for row in chk_names:
        old_def = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1",
            row["conname"],
        )
        if old_def and _CHECK_CONSTRAINT not in old_def:
            await conn.execute(
                f"ALTER TABLE api_gateway.push_destinations DROP CONSTRAINT IF EXISTS {row['conname']}"
            )
            await conn.execute(
                f"ALTER TABLE api_gateway.push_destinations ADD CHECK ({_CHECK_CONSTRAINT})"
            )
            print(f"[setup] dest_type CHECK constraint updated ✓")

    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_dest_is_active"
        " ON api_gateway.push_destinations (is_active)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_dest_type"
        " ON api_gateway.push_destinations (dest_type)"
    )

    print("[setup] api_gateway.push_destinations ✓")
