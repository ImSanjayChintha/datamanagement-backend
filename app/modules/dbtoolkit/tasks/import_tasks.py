import json
import psycopg2
from psycopg2.extras import RealDictCursor

from app.core.celery_app import celery_app
from app.core.config import settings


def _sync_dsn() -> str:
    # DATABASE_URL is asyncpg style: postgresql://...
    return settings.DATABASE_URL  # works with psycopg2 if scheme is postgresql://


@celery_app.task(bind=True, name="pim.import_products")
def import_products_task(self, family_code: str, rows: list, audit_user: str | None):
    payload = [{"family_code": family_code, "rows": rows}]
    conn = psycopg2.connect(_sync_dsn())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT pim.fn_import_products(%s::jsonb, %s) AS result",
                [json.dumps(payload), audit_user],
            )
            row = cur.fetchone()
            conn.commit()
            result = row["result"]
            if isinstance(result, str):
                result = json.loads(result)
            return {"ok": True, "job_id": self.request.id, **(result or {})}
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()