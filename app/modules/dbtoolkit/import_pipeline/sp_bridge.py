"""
Module: toolkit.import_pipeline.sp_bridge
Purpose: After DuckDB verification, invoke existing pim.fn_import_products in batches.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from app.core.config import settings
from app.modules.dbtoolkit.import_pipeline.duckdb_writer import DuckDBWriter

logger = logging.getLogger(__name__)


def invoke_import_stored_procedure(
    *,
    job_id: str,
    family_code: str,
    audit_user: str | None,
    writer: DuckDBWriter,
) -> dict[str, Any]:
    """
    Stream validated rows from DuckDB into the existing SP.

    Does not redesign SP business logic — builds the same jsonb payload shape:
      [{ "family_code": "...", "rows": [ ... ] }]
    in bounded batches so the full dataset is never held in one Python list for huge jobs.
    """
    batch_size = max(1, int(settings.IMPORT_SP_BATCH_SIZE))
    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    batches = 0

    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for json_batch in writer.fetch_row_json_batches(batch_size):
                rows = [json.loads(s) for s in json_batch]
                payload = [{"family_code": family_code, "rows": rows}]
                cur.execute(
                    "SELECT pim.fn_import_products(%s::jsonb, %s) AS result",
                    [json.dumps(payload), audit_user],
                )
                row = cur.fetchone()
                result = row["result"] if row else {}
                if isinstance(result, str):
                    result = json.loads(result)
                result = result or {}
                total_inserted += int(result.get("inserted") or 0)
                total_updated += int(result.get("updated") or 0)
                total_skipped += int(result.get("skipped") or 0)
                batches += 1
                logger.info(
                    "import SP batch job=%s batch=%s rows=%s inserted=%s updated=%s",
                    job_id,
                    batches,
                    len(rows),
                    result.get("inserted"),
                    result.get("updated"),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "ok": True,
        "batches": batches,
        "inserted": total_inserted,
        "updated": total_updated,
        "skipped": total_skipped,
        "total": total_inserted + total_updated,
    }
