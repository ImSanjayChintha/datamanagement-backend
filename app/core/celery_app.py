from celery import Celery
import os
from app.core.config import settings

# Broker/result may use dedicated Redis DBs; pub/sub notifications always use REDIS_URL.
_broker = os.getenv("CELERY_BROKER_URL") or settings.REDIS_URL
_backend = os.getenv("CELERY_RESULT_BACKEND") or settings.REDIS_URL

celery_app = Celery(
    "datamanagement",
    broker=_broker,
    backend=_backend,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)
celery_app.autodiscover_tasks(["app.modules.dbtoolkit"])
celery_app.conf.imports = (
    "app.modules.dbtoolkit.tasks.import_tasks",
    "app.modules.dbtoolkit.tasks.export_tasks",
)