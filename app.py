"""Aplicação FastAPI do Mural de Notas com Foto integrado ao Amazon S3 e PostgreSQL/SQLite."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Settings, get_settings
from database import VALID_COLORS, PostRepository, create_repository
from storage import InvalidImageError, StorageBackend, create_storage

BASE_DIR = Path(__file__).resolve().parent

STATUS_MESSAGES = {
    "created": "Nota publicada com sucesso no mural!",
    "deleted": "Nota e foto removidas do mural e do armazenamento.",
}

COLOR_LABELS = {
    "sun": "Amarelo",
    "mint": "Verde Menta",
    "sky": "Azul Céu",
    "rose": "Rosa",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Fábrica da aplicação FastAPI para produção, simulação local e testes."""
    cfg = settings or get_settings()
    repo: PostRepository = create_repository(cfg)
    storage: StorageBackend = create_storage(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repo.init_db()
        storage.ensure_ready()
        yield

    application = FastAPI(
        title="Mural de Notas com Foto (S3)",
        description="Mural colaborativo de notas estilo polaroid/post-it com upload no Amazon S3.",
        lifespan=lifespan,
    )

    application.state.settings = cfg
    application.state.repo = repo
    application.state.storage = storage

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    application.mount("/static", StaticFiles(directory=static_dir), name="static")

    if cfg.storage_backend == "local":
        cfg.local_upload_dir.mkdir(parents=True, exist_ok=True)
        application.mount(
            "/media", StaticFiles(directory=cfg.local_upload_dir), name="media"
        )

    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    def build_cards(query: str = "", color: str = "") -> list[dict[str, object]]:
        posts = repo.list_posts(limit=cfg.posts_limit, query=query.strip(), color=color)
        cards: list[dict[str, object]] = []
        for post in posts:
            cards.append(
                {
                    "id": post.id,
                    "title": post.title,
                    "texto": post.texto,
                    "author": post.author,
                    "color": post.color,
                    "color_label": COLOR_LABELS.get(post.color, "Amarelo"),
                    "s3_key": post.s3_key,
                    "created_at": post.created_at,
                    "image_url": storage.get_image_url(post.s3_key),
                }
            )
        return cards

    def render_wall(
        request: Request,
        *,
        query: str = "",
        color: str = "",
        status_code: int = 200,
        status_message: str | None = None,
        error_message: str | None = None,
        form_values: dict[str, str] | None = None,
    ) -> HTMLResponse:
        cards = build_cards(query=query, color=color)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "cards": cards,
                "query": query,
                "selected_color": color,
                "colors": COLOR_LABELS,
                "max_upload_mb": cfg.max_upload_mb,
                "storage_label": storage.backend_label,
                "db_label": repo.backend_label,
                "bucket_name": cfg.s3_bucket if cfg.storage_backend == "s3" else "local_uploads",
                "status_message": status_message,
                "error_message": error_message,
                "form_values": form_values or {"title": "", "author": "", "texto": "", "color": "sun"},
            },
            status_code=status_code,
        )

    @application.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        q: str = Query(default="", max_length=100),
        cor: str = Query(default=""),
        status: str = Query(default=""),
    ):
        active_color = cor if cor in VALID_COLORS else ""
        return render_wall(
            request,
            query=q,
            color=active_color,
            status_message=STATUS_MESSAGES.get(status),
        )

    async def handle_create_post(
        request: Request,
        texto: str = Form(...),
        foto: UploadFile = File(...),
        title: str = Form(default=""),
        author: str = Form(default=""),
        color: str = Form(default="sun"),
    ):
        clean_title = title.strip()[:80]
        clean_texto = texto.strip()[:500]
        clean_author = (author.strip() or "Anônimo")[:50]
        clean_color = color if color in VALID_COLORS else "sun"
        preserved = {
            "title": clean_title,
            "author": author.strip()[:50],
            "texto": clean_texto,
            "color": clean_color,
        }

        if not clean_texto:
            return render_wall(
                request,
                status_code=400,
                error_message="Escreva uma mensagem para fixar sua nota no mural.",
                form_values=preserved,
            )

        data = await foto.read(cfg.max_upload_bytes + 1)
        if len(data) > cfg.max_upload_bytes:
            return render_wall(
                request,
                status_code=400,
                error_message=f"A imagem excede o limite máximo de {cfg.max_upload_mb} MB.",
                form_values=preserved,
            )

        try:
            key = storage.upload_image(data, foto.filename or "foto.jpg", foto.content_type)
        except InvalidImageError as exc:
            return render_wall(
                request,
                status_code=400,
                error_message=str(exc),
                form_values=preserved,
            )

        repo.create_post(
            title=clean_title,
            texto=clean_texto,
            author=clean_author,
            color=clean_color,
            s3_key=key,
        )
        return RedirectResponse("/?status=created", status_code=303)

    application.add_api_route("/posts", handle_create_post, methods=["POST"])
    application.add_api_route("/post", handle_create_post, methods=["POST"])

    @application.post("/posts/{post_id}/delete")
    def delete_post(post_id: int):
        deleted = repo.delete_post(post_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Nota não encontrada.")
        storage.delete_image(deleted.s3_key)
        return RedirectResponse("/?status=deleted", status_code=303)

    @application.get("/api/posts")
    def api_list_posts(
        q: str = Query(default="", max_length=100),
        cor: str = Query(default=""),
    ):
        active_color = cor if cor in VALID_COLORS else ""
        return {"items": build_cards(query=q, color=active_color)}

    @application.get("/health")
    def health():
        db_ok = repo.healthcheck()
        storage_ok = storage.healthcheck()
        healthy = db_ok and storage_ok
        payload = {
            "status": "ok" if healthy else "degraded",
            "database": {"backend": repo.backend_label, "ok": db_ok},
            "storage": {"backend": storage.backend_label, "ok": storage_ok},
        }
        return JSONResponse(content=payload, status_code=200 if healthy else 503)

    return application


app = create_app()
