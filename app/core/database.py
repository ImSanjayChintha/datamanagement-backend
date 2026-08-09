"""
Module: core.database
Purpose: asyncpg connection pool management and SQL helper wrappers.

All database access in the application goes through these helpers.
JSON codecs are registered on every connection so jsonb columns
are automatically serialised/deserialised as Python dicts.
"""
import json
import asyncpg
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from app.core.config import settings

_pool: asyncpg.Pool = None


def _json_encoder(val) -> str:
    """Encode a Python value to a JSON string for asyncpg.

    Args:
        val: Value to encode. If already a string, returned as-is (the caller
             pre-serialised it). Otherwise json.dumps is called.

    Returns:
        JSON string.
    """
    if isinstance(val, str):
        return val  # already serialised by the caller
    return json.dumps(val, ensure_ascii=False)


_SEARCH_PATH = "SET search_path TO toolkit, public, admin, pim, company, ecomm, forecast"


async def _setup_codecs(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs. Called once per connection creation (init=)."""
    await conn.execute(_SEARCH_PATH)
    for pg_type in ("json", "jsonb"):
        await conn.set_type_codec(
            pg_type,
            encoder=_json_encoder,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def _apply_search_path(conn: asyncpg.Connection) -> None:
    """Re-apply search_path on every pool acquire (setup=).

    asyncpg may reset session settings between requests; this ensures
    every acquired connection always has the correct schema resolution order.
    """
    await conn.execute(_SEARCH_PATH)


async def init_pool() -> None:
    """Initialise the global asyncpg connection pool.

    Creates a pool with min 2 / max 20 connections and registers JSON codecs.
    Must be called once during application startup (lifespan handler).
    """
    global _pool
    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=2,
        max_size=20,
        init=_setup_codecs,
        setup=_apply_search_path,
    )


async def close_pool() -> None:
    """Close the global asyncpg connection pool.

    Should be called during application shutdown (lifespan handler).
    """
    global _pool
    if _pool:
        await _pool.close()


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI dependency that yields a pooled database connection.

    Yields:
        asyncpg.Connection acquired from the pool.
    """
    async with _pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_conn():
    """Async context manager for acquiring a pooled database connection.

    Useful outside of FastAPI dependency injection (e.g. background tasks).

    Yields:
        asyncpg.Connection acquired from the pool.
    """
    async with _pool.acquire() as conn:
        yield conn


async def row(conn: asyncpg.Connection, sql: str, *args):
    """Fetch a single row as a dict, or None if not found.

    Args:
        conn: Active database connection.
        sql: SQL query string (use $1, $2, ... placeholders).
        *args: Positional query parameters.

    Returns:
        Dict of the first matching row, or None.
    """
    r = await conn.fetchrow(sql, *args)
    return dict(r) if r else None


async def rows(conn: asyncpg.Connection, sql: str, *args) -> list:
    """Fetch all matching rows as a list of dicts.

    Args:
        conn: Active database connection.
        sql: SQL query string.
        *args: Positional query parameters.

    Returns:
        List of row dicts (empty list if no rows matched).
    """
    rs = await conn.fetch(sql, *args)
    return [dict(r) for r in rs]


async def val(conn: asyncpg.Connection, sql: str, *args):
    """Fetch a single scalar value.

    Args:
        conn: Active database connection.
        sql: SQL query string.
        *args: Positional query parameters.

    Returns:
        The scalar value from the first column of the first row.
    """
    return await conn.fetchval(sql, *args)


async def execute(conn: asyncpg.Connection, sql: str, *args):
    """Execute a DML or DDL statement.

    Args:
        conn: Active database connection.
        sql: SQL statement string.
        *args: Positional query parameters.

    Returns:
        PostgreSQL command status string (e.g. 'INSERT 0 1').
    """
    return await conn.execute(sql, *args)
