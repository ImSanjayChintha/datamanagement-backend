"""
SchemaStatsService — Example 1

Returns a breakdown of table/view/function counts per database schema.
Uses information_schema so it works in any PostgreSQL database with no
application-specific tables required.

Gateway endpoint example
------------------------
  Method : GET or POST
  URL    : /api/v1/run/services/schema-stats
  db_type: service
  db_object: schema_stats

Body (optional)
---------------
  {}  — all user schemas are included

Response
--------
  {
    "success": true,
    "data": {
      "schemas": [
        { "schema": "public", "tables": 3, "views": 1, "functions": 2, "total": 6 }
      ],
      "total_schemas": 1
    }
  }
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.modules.api_bridge.services.base import ServiceBase
from app.modules.api_bridge.services.registry import register

_EXCLUDED = frozenset({"pg_catalog", "information_schema", "pg_toast"})


@register
class SchemaStatsService(ServiceBase):
    name        = "schema_stats"
    description = "Returns table / view / function counts per database schema"

    async def execute(
        self,
        body:        dict[str, Any],
        path_params: dict[str, str],
        db:          asyncpg.Connection,
        user_email:  str | None,
    ) -> Any:
        tables_rows = await db.fetch(
            """
            SELECT table_schema AS schema_name,
                   table_type   AS obj_type,
                   COUNT(*)     AS cnt
            FROM   information_schema.tables
            WHERE  table_schema != ALL($1::text[])
            GROUP  BY table_schema, table_type
            ORDER  BY table_schema, table_type
            """,
            list(_EXCLUDED),
        )

        fn_rows = await db.fetch(
            """
            SELECT routine_schema AS schema_name,
                   COUNT(*)       AS cnt
            FROM   information_schema.routines
            WHERE  routine_schema != ALL($1::text[])
            GROUP  BY routine_schema
            """,
            list(_EXCLUDED),
        )

        # Aggregate into one dict per schema
        by_schema: dict[str, dict] = {}
        for r in tables_rows:
            s = r["schema_name"]
            if s not in by_schema:
                by_schema[s] = {"tables": 0, "views": 0, "functions": 0}
            if r["obj_type"] == "BASE TABLE":
                by_schema[s]["tables"] = int(r["cnt"])
            elif r["obj_type"] == "VIEW":
                by_schema[s]["views"] = int(r["cnt"])

        for r in fn_rows:
            s = r["schema_name"]
            if s not in by_schema:
                by_schema[s] = {"tables": 0, "views": 0, "functions": 0}
            by_schema[s]["functions"] = int(r["cnt"])

        result = [
            {
                "schema":    k,
                "tables":    v["tables"],
                "views":     v["views"],
                "functions": v["functions"],
                "total":     v["tables"] + v["views"] + v["functions"],
            }
            for k, v in sorted(by_schema.items())
        ]

        return {"schemas": result, "total_schemas": len(result)}
