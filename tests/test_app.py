"""Testes de integração simulando Amazon S3 em memória com `moto` e banco SQLite."""

from io import BytesIO
from pathlib import Path

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image
import pytest

from app import create_app
from config import Settings


def make_png_bytes(color: tuple[int, int, int] = (255, 190, 60)) -> bytes:
    """Gera uma imagem PNG válida em memória para testes de upload."""
    buf = BytesIO()
    Image.new("RGB", (64, 48), color=color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def s3_client_and_app(tmp_path: Path):
    """Inicializa app FastAPI conectada a um bucket S3 simulado via moto e SQLite isolado."""
    bucket = "mural-test-bucket"
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=bucket)

        settings = Settings(
            storage_backend="s3",
            s3_bucket=bucket,
            aws_region="us-east-1",
            s3_endpoint_url=None,
            s3_public_endpoint_url=None,
            s3_auto_create_bucket=False,
            s3_presigned_expires=3600,
            local_upload_dir=tmp_path / "local_uploads",
            db_backend="sqlite",
            database_url=None,
            db_host=None,
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_pass="",
            sqlite_path=tmp_path / "test_mural.db",
            max_upload_mb=1,
            posts_limit=30,
        )
        application = create_app(settings)
        with TestClient(application) as client:
            yield client, s3, bucket


def test_healthcheck_returns_ok(s3_client_and_app):
    client, _, _ = s3_client_and_app
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"]["ok"] is True
    assert data["storage"]["ok"] is True


def test_upload_list_escape_and_delete_s3_flow(s3_client_and_app):
    client, s3, bucket = s3_client_and_app
    png_bytes = make_png_bytes()

    # 1. Publica uma nota com payload potencialmente perigoso (XSS)
    resp = client.post(
        "/posts",
        data={
            "title": "Viagem <b>Teste</b>",
            "author": "Ana",
            "texto": "<script>alert('xss')</script> Lindo dia!",
            "color": "mint",
        },
        files={"foto": ("foto.png", png_bytes, "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?status=created"

    # 2. Confirma que o objeto foi gravado no S3 com ContentType correto
    objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
    assert len(objects) == 1
    s3_key = objects[0]["Key"]
    assert s3_key.startswith("uploads/") and s3_key.endswith(".png")
    head = s3.head_object(Bucket=bucket, Key=s3_key)
    assert head["ContentType"] == "image/png"

    # 3. Confirma renderização HTML escapada e URL pré-assinada
    home = client.get("/")
    assert home.status_code == 200
    assert "<script>alert('xss')</script>" not in home.text
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in home.text
    assert s3_key in home.text
    assert "X-Amz-Signature=" in home.text

    # 4. Confirma endpoint JSON e filtros
    api_resp = client.get("/api/posts", params={"q": "Lindo", "cor": "mint"})
    assert api_resp.status_code == 200
    items = api_resp.json()["items"]
    assert len(items) == 1
    post_id = items[0]["id"]
    assert items[0]["author"] == "Ana"

    # Filtro que não casa retorna lista vazia
    assert client.get("/api/posts", params={"cor": "rose"}).json()["items"] == []

    # 5. Remove a nota e verifica exclusão no DB e no S3
    del_resp = client.post(f"/posts/{post_id}/delete", follow_redirects=False)
    assert del_resp.status_code == 303
    assert client.get("/api/posts").json()["items"] == []
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []


def test_rejects_invalid_image_signature(s3_client_and_app):
    client, s3, bucket = s3_client_and_app
    fake_png = b"<html><body>not a png</body></html>"

    resp = client.post(
        "/posts",
        data={"texto": "Tentando enviar HTML disfarçado"},
        files={"foto": ("fake.png", fake_png, "image/png")},
    )
    assert resp.status_code == 400
    assert "Formato inválido" in resp.text
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []


def test_rejects_oversized_upload(s3_client_and_app):
    client, s3, bucket = s3_client_and_app
    oversized = b"\x89PNG\r\n\x1a\n" + (b"0" * (1024 * 1024 + 10))

    resp = client.post(
        "/posts",
        data={"texto": "Arquivo gigante"},
        files={"foto": ("big.png", oversized, "image/png")},
    )
    assert resp.status_code == 400
    assert "excede o limite máximo" in resp.text
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []


def test_legacy_post_route_remains_compatible(s3_client_and_app):
    client, s3, bucket = s3_client_and_app
    resp = client.post(
        "/post",
        data={"texto": "Compatibilidade com rota antiga"},
        files={"foto": ("foto.png", make_png_bytes(), "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(s3.list_objects_v2(Bucket=bucket).get("Contents", [])) == 1
