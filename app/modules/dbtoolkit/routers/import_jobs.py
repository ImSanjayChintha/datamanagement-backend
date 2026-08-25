"""
Toolkit import jobs — enqueue, history, and SSE notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_admin
from app.core.response import err, ok
from app.core.config import settings
from app.modules.dbtoolkit.import_pipeline import create_job, get_job, storage
from app.modules.dbtoolkit.import_pipeline.events import user_channel
from app.modules.dbtoolkit.import_pipeline.job_service import (
    list_jobs_for_user,
    mark_failed,
    set_celery_task_id,
)
from app.modules.dbtoolkit.tasks import import_products_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/toolkit/import", tags=["Toolkit - Import Jobs"])

_HEARTBEAT_SECONDS = 25


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in job.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    # Friendly aliases for UI recovery
    out["job_id"] = out.get("id")
    out["total_rows"] = out.get("source_rows")
    out["processed_rows"] = out.get("rows_read")
    out["success_rows"] = out.get("rows_written")
    out["failed_rows"] = out.get("rows_invalid")
    return out


def _owns_job(job: dict[str, Any], admin: dict[str, Any]) -> bool:
    job_uid = job.get("user_id")
    if job_uid is None:
        # Legacy rows without user_id — match audit email as fallback
        inserted = (job.get("inserted_by") or "").strip().lower()
        email = (admin.get("email") or "").strip().lower()
        return bool(inserted and email and inserted == email)
    try:
        return int(job_uid) == int(admin["id"])
    except (TypeError, ValueError):
        return False


@router.post("/products")
async def enqueue_products_import(body: dict = Body(...), admin=Depends(get_current_admin)):
    """
    Accept RSI payload { family_code, rows }, persist JSONL to disk, enqueue Celery(job_id).
    Redis never receives the row payload. Returns immediately with status queued.
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
    user_id = int(admin["id"])

    file_path: str | None = None
    try:
        file_path, source_rows = storage.write_rows_jsonl(job_id, rows, file_name="rows.jsonl")
        if source_rows <= 0:
            storage.cleanup_job_dir(job_id)
            return err("no valid row objects to import")

        create_job(
            job_id=job_id,
            file_id=file_id,
            family_code=family_code,
            file_path=file_path,
            file_name="rows.jsonl",
            source_rows=source_rows,
            inserted_by=audit_user,
            user_id=user_id,
        )

        try:
            task = import_products_task.delay(str(job_id))
            set_celery_task_id(job_id, task.id)
        except Exception as celery_exc:
            logger.exception(
                "import Celery enqueue failed job_id=%s user_id=%s", job_id, user_id
            )
            mark_failed(job_id, f"Celery enqueue failed: {celery_exc}")
            storage.cleanup_job_dir(job_id)
            return err("Failed to queue import job — please try again")
    except Exception as e:
        if file_path:
            storage.cleanup_job_dir(job_id)
        return err(str(e))

    logger.info(
        "import job enqueued job_id=%s user_id=%s status=queued source_rows=%s",
        job_id,
        user_id,
        source_rows,
    )
    return ok({"job_id": str(job_id), "status": "queued", "source_rows": source_rows})


@router.get("/jobs")
async def list_import_jobs(
    admin=Depends(get_current_admin),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Recovery / history — PostgreSQL source of truth for the authenticated user only."""
    jobs = list_jobs_for_user(int(admin["id"]), limit=limit, offset=offset)
    return ok([_serialize_job(j) for j in jobs])


@router.get("/jobs/{job_id}")
async def get_import_job(job_id: str, admin=Depends(get_current_admin)):
    job = get_job(job_id)
    if not job:
        return err("import job not found")
    if not _owns_job(job, admin):
        return err("import job not found")
    return ok(_serialize_job(job))


@router.get("/events")
async def import_job_events(request: Request, admin=Depends(get_current_admin)):
    """
    SSE stream for the authenticated user's import notifications.
    One connection per browser session; events come from Redis pub/sub (not PG polling).
    """
    user_id = int(admin["id"])
    channel = user_channel(user_id)
    logger.info("import SSE connected user_id=%s channel=%s", user_id, channel)

    async def event_generator() -> AsyncIterator[str]:
        client: aioredis.Redis | None = None
        pubsub = None
        try:
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            # Initial comment so proxies flush headers
            yield ": connected\n\n"
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    logger.info("import SSE client disconnected user_id=%s", user_id)
                    break
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                except Exception:
                    # Redis brief blip — keep SSE alive
                    idle_ticks += 1
                    if idle_ticks >= _HEARTBEAT_SECONDS:
                        yield ": heartbeat\n\n"
                        idle_ticks = 0
                    await asyncio.sleep(1)
                    continue

                if message is None:
                    idle_ticks += 1
                    if idle_ticks >= _HEARTBEAT_SECONDS:
                        yield ": heartbeat\n\n"
                        idle_ticks = 0
                    continue

                idle_ticks = 0
                if message.get("type") != "message":
                    continue

                raw = message.get("data")
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, json.JSONDecodeError):
                    logger.warning("import SSE bad payload user_id=%s", user_id)
                    continue

                # Defense in depth — never forward another user's event
                if int(payload.get("user_id") or -1) != user_id:
                    logger.warning(
                        "import SSE dropped foreign event user_id=%s payload_user=%s",
                        user_id,
                        payload.get("user_id"),
                    )
                    continue

                event_name = str(payload.get("event") or "IMPORT_STATUS")
                data = json.dumps(payload, default=str)
                logger.info(
                    "import SSE delivered user_id=%s event=%s job_id=%s",
                    user_id,
                    event_name,
                    payload.get("job_id"),
                )
                yield f"event: {event_name}\ndata: {data}\n\n"
        except asyncio.CancelledError:
            logger.info("import SSE cancelled user_id=%s", user_id)
            raise
        except Exception as exc:
            logger.exception("import SSE error user_id=%s error=%s", user_id, exc)
            yield f"event: error\ndata: {json.dumps({'error': 'sse_unavailable'})}\n\n"
        finally:
            try:
                if pubsub is not None:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
            except Exception:
                pass
            try:
                if client is not None:
                    await client.aclose()
            except Exception:
                pass
            logger.info("import SSE cleaned up user_id=%s", user_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
