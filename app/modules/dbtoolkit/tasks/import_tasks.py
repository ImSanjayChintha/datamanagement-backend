"""
Celery task: process one import job by id only (no rows / file bytes in Redis).
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

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="pim.import_products")
def import_products_task(self, job_id: str):
    """
    Celery payload is job_id only. Rows live on disk; progress in toolkit.import_jobs.
    On success the job storage folder is deleted (JSONL + DuckDB).
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"import job not found: {job_id}")

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
        logger.info("import job completed job_id=%s", job_id)
        return result
    except Exception as exc:
        logger.exception("import job failed job_id=%s", job_id)
        mark_failed(job_id, f"{exc}\n{traceback.format_exc()[-1500:]}")
        raise
