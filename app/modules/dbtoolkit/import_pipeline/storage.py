"""
Module: toolkit.import_pipeline.storage
Purpose: Persist import payloads on shared disk so Celery never receives row bytes.
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def ensure_storage_root() -> Path:
    root = Path(settings.IMPORT_STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str | uuid.UUID) -> Path:
    path = ensure_storage_root() / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_rows_jsonl(
    job_id: str | uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    file_name: str = "rows.jsonl",
) -> tuple[str, int]:
    """
    Write one JSON object per line. Returns (absolute_path, row_count).
    """
    dest = job_dir(job_id) / file_name
    count = 0
    with dest.open("w", encoding="utf-8") as fh:
        for row in rows:
            if not isinstance(row, dict):
                continue
            fh.write(json.dumps(row, ensure_ascii=False, default=str))
            fh.write("\n")
            count += 1
    logger.info("import storage wrote %s rows → %s", count, dest)
    return str(dest.resolve()), count


def write_bytes(job_id: str | uuid.UUID, file_name: str, content: bytes) -> str:
    """Write binary artifact (e.g. xlsx). Returns absolute path."""
    dest = job_dir(job_id) / file_name
    dest.write_bytes(content)
    logger.info("job storage wrote %s bytes → %s", len(content), dest)
    return str(dest.resolve())


def duckdb_path(job_id: str | uuid.UUID) -> Path:
    return job_dir(job_id) / "ingest.duckdb"


def cleanup_job_dir(job_id: str | uuid.UUID) -> None:
    """Remove the job folder (JSONL, DuckDB, and any other artifacts)."""
    path = ensure_storage_root() / str(job_id)
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        logger.info("import storage cleaned job_id=%s path=%s", job_id, path)
    except OSError as exc:
        logger.warning("could not remove import job dir %s: %s", path, exc)


def cleanup_job_duckdb(job_id: str | uuid.UUID) -> None:
    path = duckdb_path(job_id)
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("could not remove duckdb file %s: %s", path, exc)
