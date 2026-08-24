"""
Module: core.config
Purpose: Application-wide settings loaded from environment variables (or .env file).

Process environment variables always win over .env values so Docker / CI
overrides work without changing this file. Never hardcode secrets here —
provide only safe defaults that will fail visibly if left unchanged in
production (e.g. JWT_SECRET_KEY).
"""
import os


def _load_env_file() -> None:
    """Read backend/.env into os.environ before Settings evaluates os.getenv() calls.
    Uses setdefault so real process env vars always win (Docker, CI, etc.)."""
    env_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    )
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding='utf-8') as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_env_file()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://ecom:ecom_pass@db:5432/ecom_db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    TYPESENSE_HOST: str = os.getenv("TYPESENSE_HOST", "typesense")
    TYPESENSE_PORT: int = int(os.getenv("TYPESENSE_PORT", "8108"))
    TYPESENSE_API_KEY: str = os.getenv("TYPESENSE_API_KEY", "xyz")
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "ecommerce")
    CORS_ORIGINS: list = (
        __import__("json").loads(v)
        if (v := os.getenv("CORS_ORIGINS", '["http://localhost:3000","http://localhost:3001"]')).startswith("[")
        else v.split(",")
    )
    # Anthropic AI
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_HAIKU: str = os.getenv("CLAUDE_HAIKU", "claude-haiku-4-5-20251001")
    CLAUDE_SONNET: str = os.getenv("CLAUDE_SONNET", "claude-sonnet-4-6")

    # Email / SMTP
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "noreply@corex.com")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "CoreX Platform")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Product import pipeline (DuckDB intermediate + chunked Celery processing)
    IMPORT_STORAGE_DIR: str = os.getenv(
        "IMPORT_STORAGE_DIR",
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "storage", "imports")),
    )
    IMPORT_CHUNK_SIZE: int = int(os.getenv("IMPORT_CHUNK_SIZE", "100000"))
    IMPORT_MAX_WORKERS: int = int(os.getenv("IMPORT_MAX_WORKERS", "4"))
    IMPORT_MAX_PENDING_CHUNKS: int = int(os.getenv("IMPORT_MAX_PENDING_CHUNKS", "4"))
    IMPORT_DUCKDB_THREADS: int = int(os.getenv("IMPORT_DUCKDB_THREADS", "4"))
    IMPORT_DUCKDB_MEMORY_LIMIT: str = os.getenv("IMPORT_DUCKDB_MEMORY_LIMIT", "4GB")
    IMPORT_SP_BATCH_SIZE: int = int(os.getenv("IMPORT_SP_BATCH_SIZE", "5000"))


settings = Settings()
