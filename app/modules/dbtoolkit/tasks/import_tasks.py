"""
Celery task: process one import job by id only (no rows / file bytes in Redis).
After PostgreSQL terminal state is committed, publish a Redis notification event.
"""
from __future__ import annotations

import logging
import traceback

from app.core.celery_app import celery_app
from app.modules.dbtoolkit.import_pipeline import (
    get_job,
    mark_failed,
    run_import_ingestion,
    storage,
    update_job_status,
)
from app.modules.dbtoolkit.import_pipeline.events import (
    build_terminal_event,
    publish_import_event,
)

logger = logging.getLogger(__name__)


def _notify_terminal(job_id: str, fallback_user_id: int | None = None) -> None:
    """PG is already committed — publish best-effort Redis event for SSE clients."""
    job = get_job(job_id)
    if not job:
        logger.warning("import notify skipped — job not found job_id=%s", job_id)
        return
    if job.get("status") not in ("completed", "failed"):
        logger.warning(
            "import notify skipped — non-terminal status=%s job_id=%s",
            job.get("status"),
            job_id,
        )
        return
    if job.get("user_id") is None and fallback_user_id is not None:
        job = {**job, "user_id": fallback_user_id}
    if job.get("user_id") is None:
        logger.error(
            "import notify skipped — missing user_id job_id=%s (SSE cannot route event)",
            job_id,
        )
        return
    ok = publish_import_event(build_terminal_event(job))
    if not ok:
        logger.error("import Redis publish returned false job_id=%s", job_id)


@celery_app.task(bind=True, name="pim.import_products")
def import_products_task(self, job_id: str):
    """
    Celery payload is job_id only. Rows live on disk; progress in toolkit.import_jobs.
    On success the job storage folder is deleted (JSONL + DuckDB).
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"import job not found: {job_id}")

    raw_uid = job.get("user_id")
    try:
        user_id = int(raw_uid) if raw_uid is not None else None
    except (TypeError, ValueError):
        user_id = None

    logger.info(
        "import Celery task started job_id=%s user_id=%s celery_id=%s",
        job_id,
        user_id,
        self.request.id,
    )

    try:
        update_job_status(job_id, "processing")
        result = run_import_ingestion(job)
        update_job_status(
            job_id,
            "completed",
            metrics={
                **(result.get("duckdb") or {}),
                "stored_procedure": result.get("stored_procedure"),
            },
        )
        # DuckDB connection is closed inside run_import_ingestion; safe to wipe disk.
        storage.cleanup_job_dir(job_id)
        _notify_terminal(job_id, fallback_user_id=user_id)
        logger.info("import job completed job_id=%s user_id=%s", job_id, user_id)
        return result
    except Exception as exc:
        logger.exception("import job failed job_id=%s user_id=%s", job_id, user_id)
        mark_failed(job_id, f"{exc}\n{traceback.format_exc()[-1500:]}")
        _notify_terminal(job_id, fallback_user_id=user_id)
        raise
