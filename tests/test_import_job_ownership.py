"""Ownership checks for import job API helpers."""
from __future__ import annotations

from app.modules.dbtoolkit.routers.import_jobs import _owns_job


def test_owns_job_by_user_id():
    admin = {"id": 10, "email": "a@example.com"}
    assert _owns_job({"user_id": 10}, admin) is True
    assert _owns_job({"user_id": 11}, admin) is False


def test_owns_job_legacy_email_fallback():
    admin = {"id": 10, "email": "a@example.com"}
    assert _owns_job({"user_id": None, "inserted_by": "a@example.com"}, admin) is True
    assert _owns_job({"user_id": None, "inserted_by": "other@example.com"}, admin) is False
