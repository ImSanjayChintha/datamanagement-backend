"""
Module: toolkit.services.activity_service
Purpose: Record user-initiated toolkit activities to toolkit_activity_log.

Every write operation (create/update/delete table, field, reference) calls
log_activity after a successful commit.  Failures in the logger itself are
swallowed with a warning — a logging failure must never abort the real action.
"""
import json
import logging

import asyncpg

from app.core.database import rows

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Activity type constants — use these everywhere to avoid typos
# ---------------------------------------------------------------------------
TABLE_CREATED      = "table_created"
TABLE_UPDATED      = "table_updated"
TABLE_DELETED      = "table_deleted"
FIELD_CREATED      = "field_created"
FIELD_UPDATED      = "field_updated"
FIELD_RENAMED      = "field_renamed"
FIELD_DELETED      = "field_deleted"
REFERENCE_UPDATED  = "reference_updated"


async def log_activity(
    db: asyncpg.Connection,
    activity_type: str,
    entity_type: str,
    performed_by: str | None,
    *,
    entity_id: int | None = None,
    entity_code: str | None = None,
    schema_name: str | None = None,
    table_code: str | None = None,
    detail: dict | None = None,
) -> None:
    """Insert one activity row into toolkit_activity_log.

    Never raises — a logging failure must not abort the real operation.

    Args:
        db: Active database connection.
        activity_type: One of the *_type constants above (e.g. TABLE_CREATED).
        entity_type: 'table', 'field', or 'reference'.
        performed_by: Email/identifier of the acting user.
        entity_id: Primary key of the affected row (nullable).
        entity_code: Human-readable code of the affected entity.
        schema_name: PostgreSQL schema (pim, ecomm, …).
        table_code: For field-level events, the parent table code.
        detail: Arbitrary JSON dict with before/after or extra context.
    """
    try:
        detail_json = json.dumps(detail) if detail else None
        await db.execute(
            """INSERT INTO toolkit.toolkit_activity_log
                   (activity_type, entity_type, entity_id, entity_code,
                    schema_name, table_code, detail, performed_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)""",
            activity_type, entity_type, entity_id, entity_code,
            schema_name, table_code, detail_json, performed_by,
        )
    except Exception as exc:
        logger.warning("activity_log insert failed (%s %s): %s", activity_type, entity_code, exc)


async def list_activity(
    db: asyncpg.Connection,
    performed_by: str | None = None,
    entity_type: str | None = None,
    activity_type: str | None = None,
    table_code: str | None = None,
    schema_name: str | None = None,
    since: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return filtered, paginated activity log rows newest-first.

    Args:
        db: Active database connection.
        performed_by: Filter by actor email (partial ILIKE match).
        entity_type: Filter by entity type ('table', 'field', 'reference').
        activity_type: Filter by activity type ('field_created', …).
        table_code: Filter by parent table code.
        schema_name: Filter by schema name.
        since: ISO timestamp — only return rows after this point.
        limit: Max rows to return (capped at 500).
        offset: Pagination offset.

    Returns:
        List of activity log dicts ordered by performed_at DESC.
    """
    limit = min(limit, 500)
    conditions: list[str] = []
    params: list = []

    if performed_by:
        params.append(performed_by)
        conditions.append(f"performed_by ILIKE '%'||${len(params)}||'%'")
    if entity_type:
        params.append(entity_type)
        conditions.append(f"entity_type = ${len(params)}")
    if activity_type:
        params.append(activity_type)
        conditions.append(f"activity_type = ${len(params)}")
    if table_code:
        params.append(table_code)
        p = len(params)
        params.append(table_code)
        q = len(params)
        conditions.append(f"(table_code = ${p} OR entity_code = ${q})")
    if schema_name:
        params.append(schema_name)
        conditions.append(f"schema_name = ${len(params)}")
    if since:
        params.append(since)
        conditions.append(f"performed_at > ${len(params)}::timestamptz")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])
    limit_ph  = f"${len(params) - 1}"
    offset_ph = f"${len(params)}"

    sql = (
        f"SELECT * FROM toolkit.toolkit_activity_log "
        f"{where} "
        f"ORDER BY performed_at DESC "
        f"LIMIT {limit_ph} OFFSET {offset_ph}"
    )
    return await rows(db, sql, *params)
