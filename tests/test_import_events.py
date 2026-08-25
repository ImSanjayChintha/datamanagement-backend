"""Tests for import job notification helpers (Redis event payloads + channels)."""
from __future__ import annotations

from app.modules.dbtoolkit.import_pipeline.events import (
    build_terminal_event,
    user_channel,
)


def test_user_channel_scoped():
    assert user_channel(42) == "import:user:42"
    assert user_channel("7") == "import:user:7"


def test_build_completed_event():
    job = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "user_id": 9,
        "status": "completed",
        "job_type": "import",
        "file_name": "rows.jsonl",
        "family_code": "adapters",
        "source_rows": 100,
        "rows_written": 98,
        "rows_invalid": 2,
        "metrics": {"stored_procedure": {"total": 98, "inserted": 90, "updated": 8}},
    }
    ev = build_terminal_event(job)
    assert ev["event"] == "IMPORT_COMPLETED"
    assert ev["job_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ev["user_id"] == 9
    assert ev["status"] == "completed"
    assert ev["success_rows"] == 98
    assert ev["failed_rows"] == 2


def test_build_export_template_completed_event():
    job = {
        "id": "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee",
        "user_id": 2,
        "status": "completed",
        "job_type": "export_template",
        "file_name": "products-x-template.xlsx",
        "result_file_path": "/tmp/x.xlsx",
        "rows_written": 0,
        "rows_invalid": 0,
        "metrics": {},
    }
    ev = build_terminal_event(job)
    assert ev["event"] == "EXPORT_TEMPLATE_COMPLETED"
    assert ev["download_ready"] is True
    assert ev["job_type"] == "export_template"


def test_build_failed_event():
    job = {
        "id": "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee",
        "user_id": 3,
        "status": "failed",
        "file_name": "rows.jsonl",
        "error_message": "boom",
        "rows_written": 0,
        "rows_invalid": 0,
        "metrics": {},
    }
    ev = build_terminal_event(job)
    assert ev["event"] == "IMPORT_FAILED"
    assert ev["error_message"] == "boom"
    assert ev["user_id"] == 3
