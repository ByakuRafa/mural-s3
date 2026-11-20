"""Camada de persistência com suporte a PostgreSQL (RDS/local) e SQLite."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Protocol

import psycopg2

from config import Settings

VALID_COLORS = ("sun", "mint", "sky", "rose")


@dataclass(frozen=True)
class Post:
    """Representa uma nota com foto armazenada no mural."""

    id: int
    title: str
    texto: str
    author: str
    color: str
    s3_key: str
    created_at: str


class PostRepository(Protocol):
    """Contrato comum entre implementações PostgreSQL e SQLite."""

    backend_label: str

    def init_db(self) -> None: ...
    def list_posts(self, limit: int = 30, query: str = "", color: str = "") -> list[Post]: ...
    def create_post(
        self, title: str, texto: str, author: str, color: str, s3_key: str
    ) -> Post: ...
    def get_post(self, post_id: int) -> Post | None: ...
    def delete_post(self, post_id: int) -> Post | None: ...
    def healthcheck(self) -> bool: ...


def _row_to_post(row: tuple) -> Post:
    created = row[6]
    if isinstance(created, datetime):
        created_str = created.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    else:
        created_str = str(created or "")
    color = row[4] if row[4] in VALID_COLORS else "sun"
    return Post(
        id=int(row[0]),
        title=row[1] or "",
        texto=row[2],
        author=row[3] or "Anônimo",
        color=color,
        s3_key=row[5],
        created_at=created_str,
    )


class PostgresRepository:
    """Repositório PostgreSQL para Amazon RDS ou container Postgres local."""

    backend_label = "PostgreSQL (RDS / Container)"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self):
        if self._settings.database_url:
            return psycopg2.connect(self._settings.database_url)
        return psycopg2.connect(
            host=self._settings.db_host,
            port=self._settings.db_port,
            database=self._settings.db_name,
            user=self._settings.db_user,
            password=self._settings.db_pass,
        )

    def init_db(self) -> None:
        """Cria a tabela `posts` e garante colunas de metadados em esquemas antigos."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    texto TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT 'Anônimo',
                    color TEXT NOT NULL DEFAULT 'sun',
                    s3_key TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS author TEXT NOT NULL DEFAULT 'Anônimo'")
            cur.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS color TEXT NOT NULL DEFAULT 'sun'")
            cur.execute(
                "ALTER TABLE posts ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )

    def list_posts(self, limit: int = 30, query: str = "", color: str = "") -> list[Post]:
        clauses: list[str] = []
        params: list[object] = []

        if query:
            clauses.append("(title ILIKE %s OR texto ILIKE %s OR author ILIKE %s)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if color in VALID_COLORS:
            clauses.append("color = %s")
            params.append(color)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, title, texto, author, color, s3_key, created_at
                FROM posts
                {where_sql}
                ORDER BY id DESC
                LIMIT %s
                """,
                params,
            )
            return [_row_to_post(row) for row in cur.fetchall()]

    def create_post(self, title: str, texto: str, author: str, color: str, s3_key: str) -> Post:
        safe_color = color if color in VALID_COLORS else "sun"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO posts (title, texto, author, color, s3_key)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, title, texto, author, color, s3_key, created_at
                """,
                (title, texto, author, safe_color, s3_key),
            )
            return _row_to_post(cur.fetchone())

    def get_post(self, post_id: int) -> Post | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, texto, author, color, s3_key, created_at
                FROM posts
                WHERE id = %s
                """,
                (post_id,),
            )
            row = cur.fetchone()
            return _row_to_post(row) if row else None

    def delete_post(self, post_id: int) -> Post | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM posts
                WHERE id = %s
                RETURNING id, title, texto, author, color, s3_key, created_at
                """,
                (post_id,),
            )
            row = cur.fetchone()
            return _row_to_post(row) if row else None

    def healthcheck(self) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
        except Exception:
            return False


class SQLiteRepository:
    """Repositório SQLite leve para execução e testes locais sem dependências externas."""

    backend_label = "SQLite (Local Dev)"

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    texto TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT 'Anônimo',
                    color TEXT NOT NULL DEFAULT 'sun',
                    s3_key TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            existing_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(posts)").fetchall()
            }
            migrations = {
                "title": "ALTER TABLE posts ADD COLUMN title TEXT NOT NULL DEFAULT ''",
                "author": "ALTER TABLE posts ADD COLUMN author TEXT NOT NULL DEFAULT 'Anônimo'",
                "color": "ALTER TABLE posts ADD COLUMN color TEXT NOT NULL DEFAULT 'sun'",
                "created_at": "ALTER TABLE posts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            }
            for col, ddl in migrations.items():
                if col not in existing_cols:
                    conn.execute(ddl)

    def list_posts(self, limit: int = 30, query: str = "", color: str = "") -> list[Post]:
        clauses: list[str] = []
        params: list[object] = []

        if query:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(texto) LIKE ? OR LOWER(author) LIKE ?)")
            like = f"%{query.lower()}%"
            params.extend([like, like, like])
        if color in VALID_COLORS:
            clauses.append("color = ?")
            params.append(color)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, title, texto, author, color, s3_key, created_at
                FROM posts
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_post(row) for row in rows]

    def create_post(self, title: str, texto: str, author: str, color: str, s3_key: str) -> Post:
        safe_color = color if color in VALID_COLORS else "sun"
        created_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO posts (title, texto, author, color, s3_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, texto, author, safe_color, s3_key, created_at),
            )
            post_id = cur.lastrowid
            row = conn.execute(
                "SELECT id, title, texto, author, color, s3_key, created_at FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            return _row_to_post(row)

    def get_post(self, post_id: int) -> Post | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, texto, author, color, s3_key, created_at FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            return _row_to_post(row) if row else None

    def delete_post(self, post_id: int) -> Post | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, texto, author, color, s3_key, created_at FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            return _row_to_post(row)

    def healthcheck(self) -> bool:
        try:
            with self._connect() as conn:
                return conn.execute("SELECT 1").fetchone() == (1,)
        except Exception:
            return False


def create_repository(settings: Settings) -> PostRepository:
    """Instancia o repositório de dados conforme a configuração."""
    if settings.db_backend == "postgres":
        return PostgresRepository(settings)
    return SQLiteRepository(settings.sqlite_path)
