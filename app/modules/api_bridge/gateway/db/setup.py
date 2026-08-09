"""
Gateway database setup — idempotent, runs at startup.
The gateway runtime NEVER auto-creates tables — only reads / writes existing ones.
"""
import asyncpg

_DDL = """
CREATE SCHEMA IF NOT EXISTS api_gateway;

CREATE TABLE IF NOT EXISTS api_gateway.endpoints (
    id             BIGSERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    url_path       TEXT NOT NULL,
    method         TEXT NOT NULL DEFAULT 'GET'
                   CHECK (method IN ('GET','POST','PUT','PATCH','DELETE')),
    db_schema      TEXT,
    db_object      TEXT,
    db_type        TEXT CHECK (db_type IN ('table','view','function')),
    headers        JSONB NOT NULL DEFAULT '[]',
    body_schema    JSONB NOT NULL DEFAULT '{}',
    filters        JSONB NOT NULL DEFAULT '[]',
    columns        JSONB NOT NULL DEFAULT '[]',
    description    TEXT,
    status         TEXT NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','active','deprecated')),
    inserted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_at    TIMESTAMPTZ,
    CONSTRAINT uq_gw_endpoint UNIQUE (method, url_path)
);
"""

_MIGRATIONS = [
    # 1 — operation_type: decouple HTTP method from SQL operation
    """ALTER TABLE api_gateway.endpoints
       ADD COLUMN IF NOT EXISTS operation_type TEXT
       CHECK (operation_type IN ('select','insert','update','delete'))""",

    # 2 — config: per-endpoint overrides (pk_column, translation_schema/table)
    """ALTER TABLE api_gateway.endpoints
       ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'""",

    # 3 — expand db_type to include 'service' (Python service registry)
    #     Drop the auto-named inline constraint, then re-add with the new value set.
    "ALTER TABLE api_gateway.endpoints DROP CONSTRAINT IF EXISTS endpoints_db_type_check",

    """ALTER TABLE api_gateway.endpoints
       ADD CONSTRAINT endpoints_db_type_check
       CHECK (db_type IN ('table','view','function','service'))""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_gw_ep_status      ON api_gateway.endpoints(status)",
    "CREATE INDEX IF NOT EXISTS idx_gw_ep_method_path ON api_gateway.endpoints(method, url_path)",
]

async def create_schema_translations(conn: asyncpg.Connection, schema: str) -> None:
    """Ensure {schema}.translations exists for gateway multilingual endpoint fields.

    This is the API-gateway's per-schema translations table — completely separate
    from the toolkit's global translations system.  Safe to call multiple times.
    """
    safe_schema = schema.replace('"', '')
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{safe_schema}".translations (
            id          BIGSERIAL PRIMARY KEY,
            table_name  TEXT    NOT NULL,
            entity_id   BIGINT  NOT NULL,
            field_code  TEXT    NOT NULL,
            lang_code   TEXT    NOT NULL,
            value       TEXT,
            modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_gw_translation
                UNIQUE (table_name, entity_id, field_code, lang_code)
        )
    """)


async def setup_gateway(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)

    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
        except Exception as exc:
            print(f"[gateway] migration skip ({type(exc).__name__}): {sql[:70]}")

    for sql in _INDEXES:
        try:
            await conn.execute(sql)
        except Exception as exc:
            print(f"[gateway] index skip ({type(exc).__name__}): {sql[:70]}")

    # Self-heal: endpoints built for call_jsonb_function but missing config.returns.
    # Identified by body_schema having both p_filters and p_sort — the toolkit
    # JSONB function signature.  Merging '{"returns":"jsonb"}' is idempotent.
    tag = await conn.execute("""
        UPDATE api_gateway.endpoints
        SET    config      = config || '{"returns": "jsonb"}'::jsonb,
               modified_at = NOW()
        WHERE  db_type        = 'function'
          AND  operation_type = 'select'
          AND  (config->>'returns') IS NULL
          AND  body_schema ? 'p_filters'
          AND  body_schema ? 'p_sort'
    """)
    # tag is "UPDATE N" — print only when rows were actually changed
    n = int(tag.split()[-1]) if tag and tag.startswith("UPDATE") else 0
    if n:
        print(f"[gateway] patched {n} endpoint(s): config.returns → 'jsonb' ✓")

    print("[gateway] tables ready ✓")
