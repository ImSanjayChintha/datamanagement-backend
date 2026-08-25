"""
Module: toolkit.import_pipeline.events
Purpose: Publish lightweight import job notification events to Redis (after PG commit).
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
    Publish to Redis pub/sub. Never raises into the import pipeline —
    PG state is already the source of truth.
    """
    user_id = payload.get("user_id")
    if user_id is None:
        logger.warning("import event skipped — missing user_id job_id=%s", payload.get("job_id"))
        return False
    channel = user_channel(user_id)
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            body = json.dumps(payload, default=str)
            client.publish(channel, body)
            logger.info(
                "import Redis event published channel=%s event=%s job_id=%s user_id=%s",
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
            "import Redis publish failed job_id=%s user_id=%s error=%s",
            payload.get("job_id"),
            user_id,
            exc,
        )
        return False


def build_terminal_event(job: dict[str, Any]) -> dict[str, Any]:
    """Build IMPORT_COMPLETED / IMPORT_FAILED payload from a job row."""
    status = str(job.get("status") or "")
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
        "file_name": job.get("file_name"),
        "family_code": job.get("family_code"),
        "source_rows": job.get("source_rows"),
        "success_rows": success_rows,
        "failed_rows": failed_rows,
    }
    if status == "completed":
        return {"event": "IMPORT_COMPLETED", **base}
    if status == "failed":
        return {
            "event": "IMPORT_FAILED",
            **base,
            "error_message": (job.get("error_message") or "")[:500],
        }
    return {"event": "IMPORT_STATUS", **base}
