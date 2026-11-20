"""Configuração centralizada da aplicação Mural de Notas com Foto."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Parâmetros de ambiente para armazenamento S3, banco de dados e limites."""

    storage_backend: str
    s3_bucket: str
    aws_region: str
    s3_endpoint_url: str | None
    s3_public_endpoint_url: str | None
    s3_auto_create_bucket: bool
    s3_presigned_expires: int
    local_upload_dir: Path
    db_backend: str
    database_url: str | None
    db_host: str | None
    db_port: int
    db_name: str
    db_user: str
    db_pass: str
    sqlite_path: Path
    max_upload_mb: int
    posts_limit: int

    @property
    def max_upload_bytes(self) -> int:
        """Retorna o tamanho máximo permitido para upload em bytes."""
        return self.max_upload_mb * 1024 * 1024


def get_settings() -> Settings:
    """Carrega variáveis de ambiente (e `.env` opcional) para construir Settings."""
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL") or None
    db_host = os.environ.get("DB_HOST") or None
    explicit_db = os.environ.get("DB_BACKEND", "").strip().lower()

    if explicit_db in {"postgres", "sqlite"}:
        db_backend = explicit_db
    elif database_url and database_url.startswith("postgres"):
        db_backend = "postgres"
    elif db_host:
        db_backend = "postgres"
    else:
        db_backend = "sqlite"

    storage_backend = os.environ.get("STORAGE_BACKEND", "s3").strip().lower()
    if storage_backend not in {"s3", "local"}:
        raise ValueError(f"STORAGE_BACKEND inválido: {storage_backend!r}")

    s3_endpoint_url = os.environ.get("S3_ENDPOINT_URL") or None
    s3_public_endpoint_url = os.environ.get("S3_PUBLIC_ENDPOINT_URL") or s3_endpoint_url

    return Settings(
        storage_backend=storage_backend,
        s3_bucket=os.environ.get("S3_BUCKET", "mural-notas-dev"),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        s3_endpoint_url=s3_endpoint_url,
        s3_public_endpoint_url=s3_public_endpoint_url,
        s3_auto_create_bucket=os.environ.get("S3_AUTO_CREATE_BUCKET", "false").lower() == "true",
        s3_presigned_expires=int(os.environ.get("S3_PRESIGNED_EXPIRES", "3600")),
        local_upload_dir=Path(os.environ.get("LOCAL_UPLOAD_DIR", "local_uploads")),
        db_backend=db_backend,
        database_url=database_url,
        db_host=db_host,
        db_port=int(os.environ.get("DB_PORT", "5432")),
        db_name=os.environ.get("DB_NAME", "postgres"),
        db_user=os.environ.get("DB_USER", "postgres"),
        db_pass=os.environ.get("DB_PASS", ""),
        sqlite_path=Path(os.environ.get("SQLITE_PATH", "mural.db")),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "5")),
        posts_limit=int(os.environ.get("POSTS_LIMIT", "30")),
    )
