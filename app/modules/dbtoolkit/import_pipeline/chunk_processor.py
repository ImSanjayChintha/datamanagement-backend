"""
Module: toolkit.import_pipeline.chunk_processor
Purpose: Defensive per-chunk validation + PyArrow table build (workers only).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from app.modules.dbtoolkit.import_pipeline.chunk_reader import Chunk

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("code", "name")


@dataclass
class ProcessedChunk:
    chunk_id: int
    row_start: int
    table: pa.Table
    valid_rows: int
    invalid_rows: int
    blank_rows_skipped: int


def _is_blank_row(row: dict[str, Any]) -> bool:
    return not any(str(v).strip() for v in row.values() if v is not None)


def process_chunk(
    chunk: Chunk,
    *,
    job_id: str,
    file_id: str,
    source_file: str,
    family_code: str,
) -> ProcessedChunk:
    """
    Validate/transform one chunk into an Arrow table bound for the DuckDB writer.
    Invalid rows (missing code/name) are counted and dropped — not written.
    """
    codes: list[str] = []
    names: list[str] = []
    sort_orders: list[int] = []
    is_actives: list[bool] = []
    payloads: list[str] = []
    job_ids: list[str] = []
    file_ids: list[str] = []
    chunk_ids: list[int] = []
    row_numbers: list[int] = []
    source_files: list[str] = []
    family_codes: list[str] = []

    invalid = 0
    blank = 0

    for offset, row in enumerate(chunk.rows):
        row_number = chunk.row_start + offset
        if _is_blank_row(row):
            blank += 1
            continue

        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            invalid += 1
            continue

        sort_raw = row.get("sort_order")
        try:
            sort_order = int(sort_raw) if sort_raw not in (None, "") else 0
        except (TypeError, ValueError):
            sort_order = 0

        active_raw = str(row.get("is_active", "true")).strip().lower()
        is_active = active_raw not in ("false", "0", "no", "n")

        # Keep full original row JSON for SP (attributes stay flat keys)
        payload = dict(row)
        payload["code"] = code
        payload["name"] = name
        payload["sort_order"] = sort_order
        payload["is_active"] = is_active

        codes.append(code)
        names.append(name)
        sort_orders.append(sort_order)
        is_actives.append(is_active)
        payloads.append(json.dumps(payload, ensure_ascii=False, default=str))
        job_ids.append(job_id)
        file_ids.append(file_id)
        chunk_ids.append(chunk.chunk_id)
        row_numbers.append(row_number)
        source_files.append(source_file)
        family_codes.append(family_code)

    table = pa.table(
        {
            "job_id": pa.array(job_ids, type=pa.string()),
            "file_id": pa.array(file_ids, type=pa.string()),
            "chunk_id": pa.array(chunk_ids, type=pa.int32()),
            "row_number": pa.array(row_numbers, type=pa.int64()),
            "source_file": pa.array(source_files, type=pa.string()),
            "family_code": pa.array(family_codes, type=pa.string()),
            "code": pa.array(codes, type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "sort_order": pa.array(sort_orders, type=pa.int32()),
            "is_active": pa.array(is_actives, type=pa.bool_()),
            "row_json": pa.array(payloads, type=pa.string()),
        }
    )

    return ProcessedChunk(
        chunk_id=chunk.chunk_id,
        row_start=chunk.row_start,
        table=table,
        valid_rows=table.num_rows,
        invalid_rows=invalid,
        blank_rows_skipped=blank,
    )
