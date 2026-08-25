"""
Toolkit export — enqueue template/data Excel jobs (Celery) + download completed files.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends
from fastapi.responses import FileResponse
import asyncpg

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.response import err, ok
from app.modules.dbtoolkit.import_pipeline import create_job, get_job, storage
from app.modules.dbtoolkit.import_pipeline.job_service import set_celery_task_id, mark_failed
from app.modules.dbtoolkit.tasks.export_tasks import export_products_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/toolkit/export", tags=["Toolkit - Export"])


def _owns_job(job: dict, admin: dict) -> bool:
    job_uid = job.get("user_id")
    if job_uid is None:
        inserted = (job.get("inserted_by") or "").strip().lower()
        email = (admin.get("email") or "").strip().lower()
        return bool(inserted and email and inserted == email)
    try:
        return int(job_uid) == int(admin["id"])
    except (TypeError, ValueError):
        return False


def _enqueue_export(
    *,
    admin: dict,
    family_code: str,
    endpoint: str,
    job_type: str,
    placeholder_name: str,
) -> dict:
    family_code = (family_code or "").strip()
    endpoint = (endpoint or "").strip()
    if not family_code:
        raise ValueError("family_code is required")
    if not endpoint:
        raise ValueError("endpoint is required")

    job_id = uuid.uuid4()
    file_id = uuid.uuid4()
    user_id = int(admin["id"])
    audit_user = admin.get("email") or admin.get("full_name")

    # Placeholder path until Celery writes the xlsx
    storage.job_dir(job_id)
    pending_path = str((storage.job_dir(job_id) / ".pending").resolve())

    create_job(
        job_id=job_id,
        file_id=file_id,
        family_code=family_code,
        file_path=pending_path,
        file_name=placeholder_name,
        source_rows=0,
        inserted_by=audit_user,
        user_id=user_id,
        job_type=job_type,
        entity="products",
        endpoint_path=endpoint,
    )

    try:
        task = export_products_task.delay(str(job_id))
        set_celery_task_id(job_id, task.id)
    except Exception as celery_exc:
        logger.exception("export Celery enqueue failed job_id=%s", job_id)
        mark_failed(job_id, f"Celery enqueue failed: {celery_exc}")
        storage.cleanup_job_dir(job_id)
        raise RuntimeError("Failed to queue export job — please try again") from celery_exc

    logger.info(
        "export job enqueued job_id=%s job_type=%s user_id=%s family=%s",
        job_id,
        job_type,
        user_id,
        family_code,
    )
    return {"job_id": str(job_id), "status": "queued", "job_type": job_type}


@router.post("/template")
async def export_template(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Enqueue template generation; returns job_id immediately (download via SSE + download API)."""
    try:
        data = _enqueue_export(
            admin=admin,
            family_code=body.get("family_code") or "",
            endpoint=body.get("endpoint") or "",
            job_type="export_template",
            placeholder_name="template.xlsx",
        )
        return ok(data)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("export_template enqueue failed")
        return err(str(e))


@router.post("/data")
async def export_data(
    body: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Enqueue full data export; returns job_id immediately."""
    try:
        data = _enqueue_export(
            admin=admin,
            family_code=body.get("family_code") or "",
            endpoint=body.get("endpoint") or "/gateway/products/export-data",
            job_type="export_data",
            placeholder_name="export.xlsx",
        )
        return ok(data)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("export_data enqueue failed")
        return err(str(e))


@router.get("/jobs/{job_id}/download")
async def download_export_job(job_id: str, admin=Depends(get_current_admin)):
    job = get_job(job_id)
    if not job or not _owns_job(job, admin):
        return err("export job not found")
    if job.get("status") != "completed":
        return err("export job is not ready yet")
    path = job.get("result_file_path") or job.get("file_path")
    if not path or not Path(path).is_file():
        return err("export file missing")
    name = job.get("file_name") or Path(path).name
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=str(name),
    )
