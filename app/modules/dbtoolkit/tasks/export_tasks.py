"""
Celery tasks for async product Excel template / data export jobs.
"""
from __future__ import annotations

import logging
import traceback

from app.core.celery_app import celery_app
from app.modules.dbtoolkit.import_pipeline import get_job, mark_failed, storage, update_job_status
from app.modules.dbtoolkit.import_pipeline.events import (
    build_terminal_event,
    publish_import_event,
)
from app.modules.dbtoolkit.import_pipeline.job_service import set_result_file
from app.modules.dbtoolkit.services.export_sync import (
    build_data_export_bytes,
    build_template_bytes,
)

logger = logging.getLogger(__name__)


def _notify_terminal(job_id: str, fallback_user_id: int | None = None) -> None:
    job = get_job(job_id)
    if not job or job.get("status") not in ("completed", "failed"):
        return
    if job.get("user_id") is None and fallback_user_id is not None:
        job = {**job, "user_id": fallback_user_id}
    publish_import_event(build_terminal_event(job))


@celery_app.task(bind=True, name="pim.export_products")
def export_products_task(self, job_id: str):
    """
    job_type export_template | export_data.
    Writes xlsx under job storage; leaves file on disk for download (unlike import cleanup).
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"export job not found: {job_id}")

    job_type = str(job.get("job_type") or "")
    family_code = str(job.get("family_code") or "")
    endpoint_path = str(job.get("endpoint_path") or "")
    raw_uid = job.get("user_id")
    try:
        user_id = int(raw_uid) if raw_uid is not None else None
    except (TypeError, ValueError):
        user_id = None

    logger.info(
        "export Celery started job_id=%s job_type=%s user_id=%s family=%s",
        job_id,
        job_type,
        user_id,
        family_code,
    )

    try:
        update_job_status(job_id, "processing")
        if job_type == "export_template":
            content, filename, n = build_template_bytes(family_code, endpoint_path)
            rows_written = 0
            metrics = {"headers": n, "kind": "template"}
        elif job_type == "export_data":
            content, filename, n = build_data_export_bytes(family_code, endpoint_path)
            rows_written = n
            metrics = {"rows": n, "kind": "data"}
        else:
            raise ValueError(f"unsupported export job_type: {job_type}")

        path = storage.write_bytes(job_id, filename, content)
        set_result_file(
            job_id,
            result_file_path=path,
            file_name=filename,
            rows_written=rows_written,
        )
        update_job_status(job_id, "completed", metrics=metrics, rows_written=rows_written)
        _notify_terminal(job_id, fallback_user_id=user_id)
        logger.info(
            "export job completed job_id=%s job_type=%s file=%s rows=%s",
            job_id,
            job_type,
            filename,
            rows_written,
        )
        return {"ok": True, "job_id": job_id, "file_name": filename, "rows": rows_written}
    except Exception as exc:
        logger.exception("export job failed job_id=%s", job_id)
        mark_failed(job_id, f"{exc}\n{traceback.format_exc()[-1500:]}")
        _notify_terminal(job_id, fallback_user_id=user_id)
        raise
