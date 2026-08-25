"""
Module: dbtoolkit.import_pipeline
Purpose: Chunked product import — disk storage → parallel Arrow → DuckDB → SP.
"""
from app.modules.dbtoolkit.import_pipeline import storage
from app.modules.dbtoolkit.import_pipeline.ingestion_service import run_import_ingestion
from app.modules.dbtoolkit.import_pipeline.job_service import (
    create_job,
    get_job,
    list_jobs_for_user,
    mark_failed,
    update_job_status,
)

__all__ = [
    "create_job",
    "get_job",
    "list_jobs_for_user",
    "mark_failed",
    "update_job_status",
    "run_import_ingestion",
    "storage",
]
