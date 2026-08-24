"""
Module: toolkit.import_pipeline.job_service
Purpose: CRUD for toolkit.import_jobs (sync psycopg2 — used by API helpers + Celery).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

from app.core.config import settings

logger = logging.getLogger(__name__)
psycopg2.extras.register_uuid()


def _connect():
    return psycopg2.connect(settings.DATABASE_URL)


def create_job(
    *,
    job_id: uuid.UUID,
    file_id: uuid.UUID,
    family_code: str,
    file_path: str,
    file_name: str,
    source_rows: int,
    inserted_by: str | None,
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    sql = """
        INSERT INTO toolkit.import_jobs (
            id, entity, family_code, file_name, file_path, file_id,
            status, source_rows, inserted_by, celery_task_id
        ) VALUES (
            %s, 'products', %s, %s, %s, %s,
            'queued', %s, %s, %s
        )
        RETURNING *
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    job_id,
                    family_code,
                    file_name,
                    file_path,
                    file_id,
                    source_rows,
                    inserted_by,
                    celery_task_id,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def get_job(job_id: str | uuid.UUID) -> dict[str, Any] | None:
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM toolkit.import_jobs WHERE id = %s", (str(job_id),))
            row = cur.fetchone()
            return dict(row) if row else None


def set_celery_task_id(job_id: str | uuid.UUID, task_id: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE toolkit.import_jobs
                   SET celery_task_id = %s, modified_at = %s
                 WHERE id = %s
                """,
                (task_id, datetime.now(timezone.utc), str(job_id)),
            )
        conn.commit()


def update_job_status(
    job_id: str | uuid.UUID,
    status: str,
    *,
    error_message: str | None = None,
    rows_read: int | None = None,
    rows_written: int | None = None,
    rows_invalid: int | None = None,
    chunks_created: int | None = None,
    chunks_written: int | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    sets = ["status = %s", "modified_at = %s"]
    args: list[Any] = [status, datetime.now(timezone.utc)]

    if error_message is not None:
        sets.append("error_message = %s")
        args.append(error_message)
    if rows_read is not None:
        sets.append("rows_read = %s")
        args.append(rows_read)
    if rows_written is not None:
        sets.append("rows_written = %s")
        args.append(rows_written)
    if rows_invalid is not None:
        sets.append("rows_invalid = %s")
        args.append(rows_invalid)
    if chunks_created is not None:
        sets.append("chunks_created = %s")
        args.append(chunks_created)
    if chunks_written is not None:
        sets.append("chunks_written = %s")
        args.append(chunks_written)
    if metrics is not None:
        sets.append("metrics = %s::jsonb")
        args.append(json.dumps(metrics))

    args.append(str(job_id))
    sql = f"UPDATE toolkit.import_jobs SET {', '.join(sets)} WHERE id = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
        conn.commit()


def mark_failed(job_id: str | uuid.UUID, error_message: str) -> None:
    update_job_status(job_id, "failed", error_message=error_message[:4000])
