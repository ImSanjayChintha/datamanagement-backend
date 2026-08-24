"""
Module: toolkit.import_pipeline.ingestion_service
Purpose: Bounded-queue parallel ingestion into per-job DuckDB, then SP batches.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.modules.dbtoolkit.import_pipeline.chunk_processor import ProcessedChunk, process_chunk
from app.modules.dbtoolkit.import_pipeline.chunk_reader import Chunk, iter_csv_chunks, iter_jsonl_chunks
from app.modules.dbtoolkit.import_pipeline.duckdb_writer import DuckDBWriter
from app.modules.dbtoolkit.import_pipeline.job_service import update_job_status
from app.modules.dbtoolkit.import_pipeline.storage import duckdb_path
from app.modules.dbtoolkit.import_pipeline.sp_bridge import invoke_import_stored_procedure

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass
class IngestStats:
    chunks_created: int = 0
    chunks_validated: int = 0
    chunks_written: int = 0
    rows_read: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    blank_rows_skipped: int = 0
    rows_written: int = 0
    elapsed_seconds: float = 0.0
    rows_per_second: float = 0.0
    mb_per_second: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_processed(self, processed: ProcessedChunk) -> None:
        with self.lock:
            self.chunks_validated += 1
            self.valid_rows += processed.valid_rows
            self.invalid_rows += processed.invalid_rows
            self.blank_rows_skipped += processed.blank_rows_skipped


class IngestionError(Exception):
    pass


def _detect_reader(path: Path, chunk_size: int):
    suffix = path.suffix.lower()
    if suffix in (".jsonl", ".json"):
        # JSON array file is not streamed here — API writes JSONL
        return iter_jsonl_chunks(path, chunk_size)
    if suffix == ".csv":
        return iter_csv_chunks(path, chunk_size)
    # default: treat as JSONL (RSI path)
    return iter_jsonl_chunks(path, chunk_size)


def run_import_ingestion(job: dict[str, Any]) -> dict[str, Any]:
    """
    Full pipeline for one import job:
      stream → bounded input queue → workers → Arrow → write queue → DuckDB
      → verify → existing pim.fn_import_products in batches
    """
    job_id = str(job["id"])
    file_id = str(job["file_id"])
    family_code = str(job.get("family_code") or "")
    file_path = Path(job["file_path"])
    source_file = str(job.get("file_name") or file_path.name)

    if not family_code:
        raise IngestionError("family_code missing on import job")
    if not file_path.exists():
        raise IngestionError(f"import file not found: {file_path}")

    chunk_size = max(1, int(settings.IMPORT_CHUNK_SIZE))
    max_workers = max(1, int(settings.IMPORT_MAX_WORKERS))
    max_pending = max(1, int(settings.IMPORT_MAX_PENDING_CHUNKS))

    stats = IngestStats()
    stop_event = threading.Event()
    error_event = threading.Event()
    error_box: list[BaseException] = []

    input_q: queue.Queue = queue.Queue(maxsize=max_pending)
    output_q: queue.Queue = queue.Queue(maxsize=max_pending)

    db_file = duckdb_path(job_id)
    writer = DuckDBWriter(db_file, job_id)
    t0 = time.perf_counter()
    file_size = file_path.stat().st_size

    def _fail(exc: BaseException) -> None:
        if not error_event.is_set():
            error_box.append(exc)
            error_event.set()
            stop_event.set()

    def reader_fn() -> None:
        try:
            for chunk in _detect_reader(file_path, chunk_size):
                if stop_event.is_set():
                    break
                with stats.lock:
                    stats.chunks_created += 1
                    stats.rows_read += len(chunk.rows)
                while not stop_event.is_set():
                    try:
                        input_q.put(chunk, timeout=0.5)
                        break
                    except queue.Full:
                        continue
            # poison pills for workers
            for _ in range(max_workers):
                while not stop_event.is_set():
                    try:
                        input_q.put(_SENTINEL, timeout=0.5)
                        break
                    except queue.Full:
                        if error_event.is_set():
                            return
                        continue
        except BaseException as exc:
            _fail(exc)
            for _ in range(max_workers):
                try:
                    input_q.put_nowait(_SENTINEL)
                except queue.Full:
                    pass

    def worker_fn(worker_idx: int) -> None:
        try:
            while not stop_event.is_set():
                try:
                    item = input_q.get(timeout=0.5)
                except queue.Empty:
                    if error_event.is_set():
                        break
                    continue
                if item is _SENTINEL:
                    output_q.put(_SENTINEL)
                    return
                chunk: Chunk = item
                t_chunk = time.perf_counter()
                processed = process_chunk(
                    chunk,
                    job_id=job_id,
                    file_id=file_id,
                    source_file=source_file,
                    family_code=family_code,
                )
                stats.add_processed(processed)
                logger.info(
                    "import chunk processed job=%s chunk=%s rows=%s worker=%s duration=%.3fs",
                    job_id,
                    processed.chunk_id,
                    processed.valid_rows,
                    worker_idx,
                    time.perf_counter() - t_chunk,
                )
                while not stop_event.is_set():
                    try:
                        output_q.put(processed, timeout=0.5)
                        break
                    except queue.Full:
                        if error_event.is_set():
                            return
                        continue
        except BaseException as exc:
            _fail(exc)
            try:
                output_q.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    def writer_fn() -> None:
        sentinels = 0
        try:
            while sentinels < max_workers and not error_event.is_set():
                try:
                    item = output_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is _SENTINEL:
                    sentinels += 1
                    continue
                processed: ProcessedChunk = item
                writer.write_table(processed.table)
                with stats.lock:
                    stats.chunks_written += 1
                    stats.rows_written = writer.rows_written
        except BaseException as exc:
            _fail(exc)

    reader_thread = threading.Thread(target=reader_fn, name=f"import-reader-{job_id}", daemon=True)
    writer_thread = threading.Thread(target=writer_fn, name=f"import-writer-{job_id}", daemon=True)

    try:
        reader_thread.start()
        writer_thread.start()
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"import-w-{job_id}") as pool:
            futures = [pool.submit(worker_fn, i) for i in range(max_workers)]
            for fut in futures:
                fut.result()
        reader_thread.join(timeout=30)
        writer_thread.join(timeout=30)

        if error_event.is_set():
            raise IngestionError(str(error_box[0]) if error_box else "ingestion failed")

        # integrity checks
        if stats.chunks_created != stats.chunks_validated or stats.chunks_validated != stats.chunks_written:
            raise IngestionError(
                f"chunk count mismatch created={stats.chunks_created} "
                f"validated={stats.chunks_validated} written={stats.chunks_written}"
            )
        if stats.valid_rows != stats.rows_written:
            raise IngestionError(
                f"row count mismatch valid={stats.valid_rows} written={stats.rows_written}"
            )
        duck_count = writer.count_for_job()
        if duck_count != stats.rows_written:
            raise IngestionError(
                f"duckdb count mismatch expected={stats.rows_written} actual={duck_count}"
            )
        # source accounting (non-blank logical rows from JSONL ≈ read; blanks tracked in processor)
        accounted = stats.valid_rows + stats.invalid_rows + stats.blank_rows_skipped
        if accounted != stats.rows_read:
            # JSONL reader does not skip blanks before counting — blank lines never enter rows_read.
            # Only fail when processor saw more than read (should not happen).
            if accounted > stats.rows_read:
                raise IngestionError(
                    f"row accounting overflow read={stats.rows_read} accounted={accounted}"
                )

        writer.commit()
        elapsed = time.perf_counter() - t0
        stats.elapsed_seconds = elapsed
        stats.rows_per_second = (stats.rows_written / elapsed) if elapsed > 0 else 0.0
        stats.mb_per_second = ((file_size / (1024 * 1024)) / elapsed) if elapsed > 0 else 0.0

        duck_metrics = {
            "rows_written": stats.rows_written,
            "chunks_written": stats.chunks_written,
            "invalid_rows": stats.invalid_rows,
            "rows_read": stats.rows_read,
            "elapsed_seconds": stats.elapsed_seconds,
            "rows_per_second": stats.rows_per_second,
            "mb_per_second": stats.mb_per_second,
            "file_size_bytes": file_size,
            "workers": max_workers,
        }

        update_job_status(
            job_id,
            "duckdb_completed",
            rows_read=stats.rows_read,
            rows_written=stats.rows_written,
            rows_invalid=stats.invalid_rows,
            chunks_created=stats.chunks_created,
            chunks_written=stats.chunks_written,
            metrics=duck_metrics,
        )

        logger.info(
            "IMPORT SUMMARY job=%s file=%s size_mb=%.2f rows=%s chunks=%s workers=%s "
            "duration=%.2fs rows_per_sec=%.0f status=DUCKDB_COMPLETED",
            job_id,
            source_file,
            file_size / (1024 * 1024),
            stats.rows_written,
            stats.chunks_written,
            max_workers,
            elapsed,
            stats.rows_per_second,
        )

        update_job_status(job_id, "db_processing")
        sp_result = invoke_import_stored_procedure(
            job_id=job_id,
            family_code=family_code,
            audit_user=job.get("inserted_by"),
            writer=writer,
        )

        return {
            "ok": True,
            "job_id": job_id,
            "duckdb": duck_metrics,
            "stored_procedure": sp_result,
        }
    except BaseException:
        writer.rollback()
        raise
    finally:
        writer.close()
