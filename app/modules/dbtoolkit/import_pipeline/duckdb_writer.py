"""
Module: toolkit.import_pipeline.duckdb_writer
Purpose: Single-threaded DuckDB writer for one import job (per-job DB file).
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pyarrow as pa

from app.core.config import settings

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS final_data (
    job_id       VARCHAR,
    file_id      VARCHAR,
    chunk_id     INTEGER,
    row_number   BIGINT,
    source_file  VARCHAR,
    family_code  VARCHAR,
    code         VARCHAR,
    name         VARCHAR,
    sort_order   INTEGER,
    is_active    BOOLEAN,
    row_json     VARCHAR
);
"""


class DuckDBWriter:
    """Owns the DuckDB connection for one import job. Not thread-safe — call from writer thread only."""

    def __init__(self, db_path: str | Path, job_id: str):
        self.job_id = job_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        self.con = duckdb.connect(str(self.db_path))
        self.con.execute(f"PRAGMA threads={int(settings.IMPORT_DUCKDB_THREADS)}")
        self.con.execute(f"PRAGMA memory_limit='{settings.IMPORT_DUCKDB_MEMORY_LIMIT}'")
        self.con.execute("BEGIN")
        self.con.execute(_DDL)
        # remove any leftover rows for this job_id (fresh file, but keep pattern)
        self.con.execute("DELETE FROM final_data WHERE job_id = ?", [job_id])
        self.chunks_written = 0
        self.rows_written = 0

    def write_table(self, table: pa.Table) -> None:
        if table.num_rows == 0:
            self.chunks_written += 1
            return
        self.con.register("_batch", table)
        self.con.execute("INSERT INTO final_data SELECT * FROM _batch")
        self.con.unregister("_batch")
        self.chunks_written += 1
        self.rows_written += table.num_rows

    def count_for_job(self) -> int:
        row = self.con.execute(
            "SELECT COUNT(*) FROM final_data WHERE job_id = ?",
            [self.job_id],
        ).fetchone()
        return int(row[0]) if row else 0

    def fetch_row_json_batches(self, batch_size: int):
        """Yield lists of row_json strings ordered by row_number."""
        offset = 0
        while True:
            rows = self.con.execute(
                """
                SELECT row_json
                  FROM final_data
                 WHERE job_id = ?
                 ORDER BY row_number
                 LIMIT ? OFFSET ?
                """,
                [self.job_id, batch_size, offset],
            ).fetchall()
            if not rows:
                break
            yield [r[0] for r in rows]
            offset += len(rows)

    def commit(self) -> None:
        self.con.execute("COMMIT")

    def rollback(self) -> None:
        try:
            self.con.execute("ROLLBACK")
        except Exception as exc:
            logger.warning("duckdb rollback: %s", exc)
        try:
            self.con.execute("DELETE FROM final_data WHERE job_id = ?", [self.job_id])
            self.con.execute("CHECKPOINT")
        except Exception as exc:
            logger.warning("duckdb cleanup delete: %s", exc)

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass
