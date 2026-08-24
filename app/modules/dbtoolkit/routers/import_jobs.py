"""
Toolkit import jobs — enqueue product imports (disk + Celery job_id only).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends

from app.core.deps import get_current_admin
from app.core.response import err, ok
from app.modules.dbtoolkit.import_pipeline import create_job, get_job, storage
from app.modules.dbtoolkit.import_pipeline.job_service import set_celery_task_id
from app.modules.dbtoolkit.tasks import import_products_task

router = APIRouter(prefix="/admin/toolkit/import", tags=["Toolkit - Import Jobs"])


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in job.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


@router.post("/products")
async def enqueue_products_import(body: dict = Body(...), admin=Depends(get_current_admin)):
    """
    Accept RSI payload { family_code, rows }, persist JSONL to disk, enqueue Celery(job_id).
    Redis never receives the row payload.
    """
    family_code = (body.get("family_code") or "").strip()
    rows = body.get("rows") or []
    if not family_code:
        return err("family_code is required")
    if not isinstance(rows, list) or not rows:
        return err("rows must be a non-empty array")

    job_id = uuid.uuid4()
    file_id = uuid.uuid4()
    audit_user = admin.get("email") or admin.get("full_name")

    try:
        file_path, source_rows = storage.write_rows_jsonl(job_id, rows, file_name="rows.jsonl")
        if source_rows <= 0:
            return err("no valid row objects to import")

        create_job(
            job_id=job_id,
            file_id=file_id,
            family_code=family_code,
            file_path=file_path,
            file_name="rows.jsonl",
            source_rows=source_rows,
            inserted_by=audit_user,
        )

        task = import_products_task.delay(str(job_id))
        set_celery_task_id(job_id, task.id)
    except Exception as e:
        return err(str(e))

    return ok({"job_id": str(job_id), "status": "queued", "source_rows": source_rows})


@router.get("/jobs/{job_id}")
async def get_import_job(job_id: str, admin=Depends(get_current_admin)):
    job = get_job(job_id)
    if not job:
        return err("import job not found")
    return ok(_serialize_job(job))
