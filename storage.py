"""Camada de armazenamento de imagens com suporte a Amazon S3 / LocalStack e fallback local."""

import os
from pathlib import Path
from typing import Protocol
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import Settings

ALLOWED_IMAGE_TYPES: dict[str, tuple[str, ...]] = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
    "image/gif": (".gif",),
}


class InvalidImageError(ValueError):
    """Erro levantado quando o arquivo enviado não é uma imagem válida ou excede o limite."""


class StorageBackend(Protocol):
    """Contrato para backends de armazenamento de mídia."""

    backend_label: str

    def ensure_ready(self) -> None: ...
    def upload_image(self, data: bytes, filename: str, content_type: str | None) -> str: ...
    def get_image_url(self, key: str) -> str: ...
    def delete_image(self, key: str) -> None: ...
    def healthcheck(self) -> bool: ...


def detect_image_format(data: bytes) -> tuple[str, str] | None:
    """Valida a assinatura binária (magic bytes) e retorna `(mime_type, ext)`."""
    if data.startswith(b"\xff\xd8\xff"):
        return ("image/jpeg", ".jpg")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("image/png", ".png")
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ("image/gif", ".gif")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("image/webp", ".webp")
    return None


def validate_image_payload(
    data: bytes, filename: str, content_type: str | None, max_bytes: int
) -> tuple[str, str]:
    """Garante tamanho máximo, tipo MIME e assinatura binária válida da imagem."""
    if not data:
        raise InvalidImageError("O arquivo de imagem está vazio.")
    if len(data) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise InvalidImageError(f"A imagem excede o limite máximo de {max_mb} MB.")

    detected = detect_image_format(data)
    if not detected:
        raise InvalidImageError("Formato inválido. Envie uma imagem JPG, PNG, WEBP ou GIF.")

    detected_mime, default_ext = detected
    if content_type and content_type.startswith("image/") and content_type not in ALLOWED_IMAGE_TYPES:
        raise InvalidImageError("Tipo de imagem não suportado. Use JPG, PNG, WEBP ou GIF.")

    raw_ext = Path(filename or "").suffix.lower()
    allowed_exts = ALLOWED_IMAGE_TYPES[detected_mime]
    ext = raw_ext if raw_ext in allowed_exts else default_ext
    return detected_mime, ext


class S3Storage:
    """Armazenamento usando Amazon S3 ou simulador compatível (LocalStack / MinIO)."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._expires = settings.s3_presigned_expires
        self._auto_create = settings.s3_auto_create_bucket
        self._endpoint_url = settings.s3_endpoint_url
        self._public_endpoint_url = settings.s3_public_endpoint_url
        self._max_upload_bytes = settings.max_upload_bytes

        if self._endpoint_url:
            self.backend_label = f"S3 Simulado ({self._endpoint_url})"
        else:
            self.backend_label = f"Amazon S3 ({settings.aws_region})"

        client_kwargs: dict[str, object] = {"region_name": settings.aws_region}
        if self._endpoint_url:
            # Addressing style "path" evita problemas de DNS virtual-host em LocalStack/MinIO.
            client_kwargs["config"] = Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            )
            # Credenciais fictícias padrão apenas quando usando endpoint local sem credenciais reais.
            client_kwargs["aws_access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID", "test")
            client_kwargs["aws_secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")
        else:
            client_kwargs["config"] = Config(signature_version="s3v4")

        self._client = boto3.client("s3", endpoint_url=self._endpoint_url, **client_kwargs)
        if self._public_endpoint_url and self._public_endpoint_url != self._endpoint_url:
            self._presign_client = boto3.client(
                "s3", endpoint_url=self._public_endpoint_url, **client_kwargs
            )
        else:
            self._presign_client = self._client

    def ensure_ready(self) -> None:
        """Garante a existência do bucket quando `S3_AUTO_CREATE_BUCKET=true`."""
        if not self._auto_create:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            create_kwargs: dict[str, object] = {"Bucket": self._bucket}
            region = self._client.meta.region_name
            if region and region != "us-east-1":
                create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
            self._client.create_bucket(**create_kwargs)

    def upload_image(self, data: bytes, filename: str, content_type: str | None) -> str:
        """Envia a imagem validada ao bucket S3 e retorna a chave gerada."""
        mime_type, ext = validate_image_payload(
            data, filename, content_type, max_bytes=self._max_upload_bytes
        )
        key = f"uploads/{uuid.uuid4()}{ext}"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=mime_type,
            CacheControl="public, max-age=31536000, immutable",
        )
        return key

    def get_image_url(self, key: str) -> str:
        """Gera uma URL pré-assinada temporária de leitura no S3."""
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._expires,
        )

    def delete_image(self, key: str) -> None:
        """Remove o objeto do bucket S3."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def healthcheck(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False


class LocalDiskStorage:
    """Fallback em disco local para visualizar a interface sem S3 ou containers."""

    backend_label = "Disco Local (Dev Preview)"

    def __init__(self, base_dir: Path, max_upload_bytes: int = 5 * 1024 * 1024) -> None:
        self._base_dir = base_dir
        self._max_upload_bytes = max_upload_bytes

    def ensure_ready(self) -> None:
        (self._base_dir / "uploads").mkdir(parents=True, exist_ok=True)

    def upload_image(self, data: bytes, filename: str, content_type: str | None) -> str:
        _, ext = validate_image_payload(
            data, filename, content_type, max_bytes=self._max_upload_bytes
        )
        key = f"uploads/{uuid.uuid4()}{ext}"
        target = self._base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def get_image_url(self, key: str) -> str:
        return f"/media/{key}"

    def delete_image(self, key: str) -> None:
        target = self._base_dir / key
        if target.exists():
            target.unlink()

    def healthcheck(self) -> bool:
        return self._base_dir.exists()


def create_storage(settings: Settings) -> StorageBackend:
    """Instancia o backend de armazenamento configurado."""
    if settings.storage_backend == "local":
        return LocalDiskStorage(settings.local_upload_dir, settings.max_upload_bytes)
    return S3Storage(settings)
