"""
Module: db.schema_export
Regenerates the user-tables section of app/db/migrations/schema.sql after
toolkit DDL operations. Execution is always fire-and-forget — failures are
logged but never surface to the caller.

Run schema.sql manually in psql/pgAdmin to apply schema to a new environment.
"""
import asyncio
import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "migrations" / "schema.sql"
MARKER = "-- ══ USER TABLES (auto-generated — do not edit below) ══"


def schedule_schema_export() -> None:
    """Schedule schema.sql regeneration as a background task."""
    try:
        asyncio.create_task(_run_export())
    except RuntimeError:
        pass  # no running event loop (e.g. tests) — skip silently


async def _run_export() -> None:
    from app.core.database import get_conn
    try:
        async with get_conn() as db:
            await _regenerate(db)
    except Exception as exc:
        logger.warning("schema_export: %s", exc)


async def _regenerate(db: asyncpg.Connection) -> None:
    tables = await db.fetch("""
        SELECT schema_name, code, label
        FROM   toolkit.toolkit_tables
        WHERE  is_active = TRUE AND is_system = FALSE
        ORDER  BY schema_name, sort_order, code
    """)

    blocks: list[str] = [f"\n\n{MARKER}\n"]
    seen_schemas: set[str] = set()

    for t in tables:
        schema, code, label = t["schema_name"], t["code"], t["label"]

        if schema not in seen_schemas:
            seen_schemas.add(schema)
            blocks.append(
                f"\n-- ── {schema} ─────────────────────────────────────────────────────\n"
                f"CREATE SCHEMA IF NOT EXISTS {schema};\n"
            )

        blocks.append(f"\n-- {label}\n")

        ct = await _create_table_sql(db, schema, code)
        if ct:
            blocks.append(ct)

        v = await _view_sql(db, schema, code)
        if v:
            blocks.append("\n" + v)

        for fn in await _function_sqls(db, schema, code):
            blocks.append("\n" + fn + "\n")

    new_section = "".join(blocks)

    if SCHEMA_FILE.exists():
        existing = SCHEMA_FILE.read_text(encoding="utf-8")
        base = existing[: existing.index(MARKER)] if MARKER in existing else existing
        content = base + new_section
    else:
        content = new_section

    SCHEMA_FILE.write_text(content, encoding="utf-8")
    logger.info("schema.sql updated (%d user tables)", len(tables))


async def _create_table_sql(db: asyncpg.Connection, schema: str, code: str) -> str:
    cols = await db.fetch("""
        SELECT a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
               a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid) AS col_default,
               EXISTS (
                   SELECT 1 FROM pg_constraint c
                   WHERE  c.conrelid = a.attrelid
                     AND  ARRAY[a.attnum] <@ c.conkey
                     AND  c.contype = 'p'
               ) AS is_pk
        FROM   pg_catalog.pg_attribute a
        JOIN   pg_catalog.pg_class c     ON c.oid = a.attrelid
        JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE  n.nspname = $1 AND c.relname = $2
          AND  a.attnum > 0 AND NOT a.attisdropped
        ORDER  BY a.attnum
    """, schema, code)

    if not cols:
        return ""

    col_defs: list[str] = []
    for col in cols:
        name    = col["attname"]
        dtype   = col["data_type"]
        notnull = col["attnotnull"]
        default = col["col_default"] or ""
        is_pk   = col["is_pk"]

        if "nextval" in default and is_pk:
            sql_type = "BIGSERIAL" if "bigint" in dtype else "SERIAL"
            col_defs.append(f"    {name} {sql_type} PRIMARY KEY")
            continue

        parts: list[str] = [f"    {name}", dtype]
        if notnull:
            parts.append("NOT NULL")
        if default:
            parts.append(f"DEFAULT {default}")
        col_defs.append(" ".join(parts))

    uq_rows = await db.fetch("""
        SELECT array_agg(a.attname ORDER BY a.attnum) AS cols
        FROM   pg_constraint c
        JOIN   pg_attribute a  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        JOIN   pg_class cl     ON cl.oid = c.conrelid
        JOIN   pg_namespace n  ON n.oid = cl.relnamespace
        WHERE  n.nspname = $1 AND cl.relname = $2 AND c.contype = 'u'
        GROUP  BY c.conname
    """, schema, code)

    for uq in uq_rows:
        col_defs.append(f"    UNIQUE ({', '.join(uq['cols'])})")

    return (
        f"CREATE TABLE IF NOT EXISTS {schema}.{code} (\n"
        + ",\n".join(col_defs)
        + "\n);\n"
    )


async def _view_sql(db: asyncpg.Connection, schema: str, code: str) -> str:
    r = await db.fetchrow(
        "SELECT definition FROM pg_views WHERE schemaname=$1 AND viewname=$2",
        schema, f"v_{code}",
    )
    if not r:
        return ""
    return (
        f"DROP VIEW IF EXISTS {schema}.v_{code} CASCADE;\n"
        f"CREATE VIEW {schema}.v_{code} AS\n"
        f"{r['definition'].rstrip(';').rstrip()};\n"
    )


async def _function_sqls(db: asyncpg.Connection, schema: str, code: str) -> list[str]:
    rows = await db.fetch("""
        SELECT pg_get_functiondef(p.oid) AS def
        FROM   pg_proc p
        JOIN   pg_namespace n ON n.oid = p.pronamespace
        WHERE  n.nspname = $1 AND p.proname IN ($2, $3)
        ORDER  BY p.proname
    """, schema, f"fn_list_{code}", f"fn_get_{code}")

    result = []
    for r in rows:
        fn = r["def"].strip()
        if not fn.endswith(";"):
            fn += ";"
        result.append(fn)
    return result
