"""
Module: toolkit.import_pipeline.events
Purpose: Publish lightweight job notification events to Redis (after PG commit).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "import:user:"


def user_channel(user_id: int | str) -> str:
    return f"{CHANNEL_PREFIX}{user_id}"


def publish_import_event(payload: dict[str, Any]) -> bool:
    """
    Publish to Redis pub/sub. Never raises into the job pipeline —
    PG state is already the source of truth.
    """
    user_id = payload.get("user_id")
    if user_id is None:
        logger.warning("job event skipped — missing user_id job_id=%s", payload.get("job_id"))
        return False
    channel = user_channel(user_id)
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            body = json.dumps(payload, default=str)
            client.publish(channel, body)
            logger.info(
                "job Redis event published channel=%s event=%s job_id=%s user_id=%s",
                channel,
                payload.get("event"),
                payload.get("job_id"),
                user_id,
            )
            return True
        finally:
            client.close()
    except Exception as exc:
        logger.error(
            "job Redis publish failed job_id=%s user_id=%s error=%s",
            payload.get("job_id"),
            user_id,
            exc,
        )
        return False


def build_terminal_event(job: dict[str, Any]) -> dict[str, Any]:
    """Build terminal SSE payload from a job row (import or export)."""
    status = str(job.get("status") or "")
    job_type = str(job.get("job_type") or "import")
    metrics = job.get("metrics") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except json.JSONDecodeError:
            metrics = {}

    sp = metrics.get("stored_procedure") if isinstance(metrics, dict) else None
    sp = sp if isinstance(sp, dict) else {}

    success_rows = int(sp.get("total") or job.get("rows_written") or 0)
    failed_rows = int(job.get("rows_invalid") or 0)

    base = {
        "job_id": str(job.get("id")),
        "user_id": int(job["user_id"]) if job.get("user_id") is not None else None,
        "status": status,
        "job_type": job_type,
        "file_name": job.get("file_name"),
        "family_code": job.get("family_code"),
        "source_rows": job.get("source_rows"),
        "success_rows": success_rows,
        "failed_rows": failed_rows,
        "download_ready": bool(job.get("result_file_path")) and status == "completed",
    }

    if status == "failed":
        event = {
            "import": "IMPORT_FAILED",
            "export_template": "EXPORT_TEMPLATE_FAILED",
            "export_data": "EXPORT_DATA_FAILED",
        }.get(job_type, "JOB_FAILED")
        return {
            "event": event,
            **base,
            "error_message": (job.get("error_message") or "")[:500],
        }

    if status == "completed":
        event = {
            "import": "IMPORT_COMPLETED",
            "export_template": "EXPORT_TEMPLATE_COMPLETED",
            "export_data": "EXPORT_DATA_COMPLETED",
        }.get(job_type, "JOB_COMPLETED")
        return {"event": event, **base}

    return {"event": "JOB_STATUS", **base}
