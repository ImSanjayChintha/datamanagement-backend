

from fastapi import APIRouter, Body, Depends
from app.core.response import ok, err
from app.core.deps import get_current_admin
from app.modules.dbtoolkit.tasks import import_products_task
from app.core.celery_app import celery_app

router = APIRouter(prefix="/admin/toolkit/import", tags=["Toolkit - Import Jobs"])

@router.post("/products")
async def enqueue_products_import(body: dict = Body(...), admin=Depends(get_current_admin)):
    family_code = (body.get("family_code") or "").strip()
    rows = body.get("rows") or []
    if not family_code:
        return err("family_code is required")
    if not isinstance(rows, list) or not rows:
        return err("rows must be a non-empty array")

    task = import_products_task.delay(
        family_code,
        rows,
        admin.get("email") or admin.get("full_name"),
    )
    return ok({"job_id": task.id, "status": "queued"})


@router.get("/jobs/{job_id}")
async def get_import_job(job_id: str, admin=Depends(get_current_admin)):
    async_result = celery_app.AsyncResult(job_id)
    status = async_result.status  # PENDING | STARTED | SUCCESS | FAILURE
    data = {
        "job_id": job_id,
        "status": status.lower(),
    }
    if async_result.successful():
        data["result"] = async_result.result
    if async_result.failed():
        data["error"] = str(async_result.result)
    return ok(data)