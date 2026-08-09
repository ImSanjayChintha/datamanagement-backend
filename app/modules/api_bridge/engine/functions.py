"""Whitelisted PostgreSQL function dispatch for /api/{schema}/fn/{name}."""
from __future__ import annotations

import json
import re

import asyncpg

from .errors import EngineError

_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]{0,62}$')


async def execute_function(
    fn_name:    str,
    body:       dict,
    db:         asyncpg.Connection,
    user_email: str | None,
) -> dict:
    """Call a whitelisted PostgreSQL function and return its rows.

    The fn_name is sourced from the registry ObjectMeta, never from the URL path
    directly — the router resolves the code via registry.get() first.

    Named params are passed positionally in iteration order of body["params"].
    For production use, parameter signatures should be introspected from
    information_schema.parameters, but the named-param approach below is safe
    because fn_name comes from the registry (not the user).
    """
    params_dict: dict = body.get("params") or {}

    if not params_dict:
        sql  = f"SELECT * FROM {fn_name}()"
        rows = await db.fetch(sql)
    else:
        keys = list(params_dict.keys())
        for k in keys:
            if not _IDENTIFIER_RE.fullmatch(k):
                raise ValueError(
                    f"Invalid parameter name '{k}': must be a valid SQL identifier "
                    f"([A-Za-z_][A-Za-z0-9_$]{{0,62}})."
                )
        vals = [params_dict[k] for k in keys]
        named_parts = [f"{k} => ${i + 1}" for i, k in enumerate(keys)]
        sql  = f"SELECT * FROM {fn_name}({', '.join(named_parts)})"
        rows = await db.fetch(sql, *vals)

    return {"ok": True, "rows": [dict(r) for r in rows]}


async def execute_write_function(
    fn_name:    str,
    payload:    list | dict,
    db:         asyncpg.Connection,
    user_email: str | None,
    param_name: str = "p_data",
) -> dict:
    """Call a write-capable PG function and return its jsonb result.

    fn_name must be fully qualified: "schema.function_name"

    Expected function signatures:
      insert / upsert / sync:  fn(p_data   jsonb, p_audit_user text) → jsonb
      delete:                  fn(p_filter jsonb, p_audit_user text) → jsonb

    Pass param_name="p_filter" for delete operations.
    """
    sql = f"SELECT {fn_name}({param_name} => $1::jsonb, p_audit_user => $2) AS result"
    row = await db.fetchrow(sql, json.dumps(payload), user_email)
    if not row:
        return {"ok": True}
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    return result if isinstance(result, dict) else {"ok": True, "result": result}
