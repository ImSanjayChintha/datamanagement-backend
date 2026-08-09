"""
Module: company.db.setup
Purpose: Regenerate views and functions for all active, non-system company
         schema tables at application startup.

For each table in toolkit.toolkit_tables WHERE schema_name='company' the
builder creates (or replaces):
  - company.v_{table_code}
  - company.fn_list_{table_code}
  - company.fn_get_{table_code}

Called from app.db.setup.run_seeds() after the toolkit catalog is ready.
"""
import logging

import asyncpg

from app.modules.dbtoolkit.core.ddl import (
    build_create_view,
    build_list_function,
    build_get_function,
    build_add_column,
    pg_type,
    safe_name,
)

logger = logging.getLogger(__name__)

_SCHEMA = "company"

# (code, iso3, iso2, name, flag_icon, sort_order)
_DEFAULT_LANGUAGES: list[tuple] = [
    ("en", "eng", "en", "English",            "🇬🇧", 1),
    ("fr", "fra", "fr", "French",             "🇫🇷", 2),
    ("es", "spa", "es", "Spanish",            "🇪🇸", 3),
    ("de", "deu", "de", "German",             "🇩🇪", 4),
    ("it", "ita", "it", "Italian",            "🇮🇹", 5),
    ("pt", "por", "pt", "Portuguese",         "🇵🇹", 6),
    ("ar", "ara", "ar", "Arabic",             "🇸🇦", 7),
    ("nl", "nld", "nl", "Dutch",              "🇳🇱", 8),
    ("zh", "zho", "zh", "Chinese Simplified", "🇨🇳", 9),
    ("ja", "jpn", "ja", "Japanese",           "🇯🇵", 10),
]


async def setup_company(conn: asyncpg.Connection) -> None:
    print("[setup] ── company schema: regenerating views/functions ─────────────")

    await conn.execute("CREATE SCHEMA IF NOT EXISTS company")

    tables = await conn.fetch(
        """
        SELECT * FROM toolkit.toolkit_tables
        WHERE schema_name = $1
          AND is_active   = TRUE
          AND is_system   = FALSE
        ORDER BY sort_order, code
        """,
        _SCHEMA,
    )

    if not tables:
        print("[setup] no active company tables found — skipping")
        return

    for tbl_row in tables:
        table = dict(tbl_row)
        code  = table["code"]

        fields_rows = await conn.fetch(
            """
            SELECT f.*
            FROM   toolkit.toolkit_fields f
            JOIN   toolkit.toolkit_tables t ON t.id = f.table_id
            WHERE  t.code = $1
            ORDER  BY f.sort_order
            """,
            code,
        )
        fields = [dict(r) for r in fields_rows]

        actual_col_rows = await conn.fetch(
            """
            SELECT column_name
            FROM   information_schema.columns
            WHERE  table_schema = $1 AND table_name = $2
            """,
            _SCHEMA,
            code,
        )
        actual_cols: set[str] | None = (
            {r["column_name"] for r in actual_col_rows} if actual_col_rows else None
        )

        # Catch-up: add any catalog columns missing from the physical table
        for f in fields:
            if pg_type(f) is None or f.get("field_type") == "daterange":
                continue
            fc = safe_name(f["code"])
            if actual_cols is not None and fc not in actual_cols:
                try:
                    await conn.execute(build_add_column(table, f))
                    actual_cols.add(fc)
                    print(f"[setup]   added missing column {_SCHEMA}.{code}.{fc} ✓")
                except Exception as exc:
                    logger.warning("[setup] add column %s.%s.%s failed: %s", _SCHEMA, code, fc, exc)

        try:
            view_sql = build_create_view(table, fields, actual_cols=actual_cols)
            await conn.execute(view_sql)
            print(f"[setup]   {_SCHEMA}.v_{code} ✓")
        except Exception as exc:
            logger.warning("[setup] company.v_%s failed: %s", code, exc)

        try:
            list_sql = build_list_function(table, fields, actual_cols=actual_cols)
            await conn.execute(list_sql)
            print(f"[setup]   {_SCHEMA}.fn_list_{code} ✓")
        except Exception as exc:
            logger.warning("[setup] company.fn_list_%s failed: %s", code, exc)

        try:
            get_sql = build_get_function(table, fields, actual_cols=actual_cols)
            await conn.execute(get_sql)
            print(f"[setup]   {_SCHEMA}.fn_get_{code} ✓")
        except Exception as exc:
            logger.warning("[setup] company.fn_get_%s failed: %s", code, exc)

    # ── Seed company_languages if the table exists and is empty ──────────────
    lang_table_exists = await conn.fetchval(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema='company' AND table_name='company_languages'"""
    )
    if lang_table_exists:
        # Ensure columns exist regardless of whether the toolkit catalog is present
        for col_ddl in [
            "ALTER TABLE company.company_languages ADD COLUMN IF NOT EXISTS name text DEFAULT ''",
            "ALTER TABLE company.company_languages ADD COLUMN IF NOT EXISTS flag_icon text DEFAULT ''",
            "ALTER TABLE company.company_languages ADD COLUMN IF NOT EXISTS iso3 text DEFAULT ''",
            "ALTER TABLE company.company_languages ADD COLUMN IF NOT EXISTS iso2 text DEFAULT ''",
        ]:
            try:
                await conn.execute(col_ddl)
            except Exception as exc:
                logger.warning("[setup] company_languages column ensure failed: %s", exc)

        count = await conn.fetchval("SELECT COUNT(*) FROM company.company_languages")
        if not count:
            for code, iso3, iso2, name, flag_icon, sort_order in _DEFAULT_LANGUAGES:
                await conn.execute(
                    """INSERT INTO company.company_languages
                           (code, iso3, iso2, name, flag_icon, sort_order, is_active)
                       VALUES ($1,$2,$3,$4,$5,$6,TRUE)
                       ON CONFLICT (code) DO NOTHING""",
                    code, iso3, iso2, name, flag_icon, sort_order,
                )
            print(f"[setup] seeded {len(_DEFAULT_LANGUAGES)} company_languages rows ✓")

    print("[setup] ── company schema ready ─────────────────────────────────────")
