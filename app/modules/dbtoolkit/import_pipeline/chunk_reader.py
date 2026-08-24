"""
Module: toolkit.import_pipeline.chunk_reader
Purpose: Stream JSONL / CSV into bounded chunks without loading the full file.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    chunk_id: int
    row_start: int  # 1-based inclusive
    rows: list[dict[str, Any]]


def iter_jsonl_chunks(path: str | Path, chunk_size: int) -> Iterator[Chunk]:
    """Yield logical JSONL row chunks. Blank lines are skipped."""
    chunk_id = 0
    buf: list[dict[str, Any]] = []
    row_number = 0  # counts non-blank source records
    row_start = 1

    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row_number += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSONL at logical row {row_number}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL row {row_number} must be an object")
            buf.append(obj)
            if len(buf) >= chunk_size:
                yield Chunk(chunk_id=chunk_id, row_start=row_start, rows=buf)
                chunk_id += 1
                row_start = row_number + 1
                buf = []

    if buf:
        yield Chunk(chunk_id=chunk_id, row_start=row_start, rows=buf)


def iter_csv_chunks(
    path: str | Path,
    chunk_size: int,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> Iterator[Chunk]:
    """Stream CSV with csv.DictReader (quoted fields, delimiters in quotes)."""
    chunk_id = 0
    buf: list[dict[str, Any]] = []
    row_number = 0
    row_start = 1

    with Path(path).open("r", encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        for raw in reader:
            row_number += 1
            # skip fully blank rows
            if not any((v or "").strip() for v in raw.values()):
                continue
            buf.append({k: (v if v is not None else "") for k, v in raw.items()})
            if len(buf) >= chunk_size:
                yield Chunk(chunk_id=chunk_id, row_start=row_start, rows=buf)
                chunk_id += 1
                row_start = row_number + 1
                buf = []

    if buf:
        yield Chunk(chunk_id=chunk_id, row_start=row_start, rows=buf)
