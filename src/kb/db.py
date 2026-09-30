"""Database connection, schema setup and per-user sessions."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from kb.config import Settings

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
VECTOR_SQL = SQL_DIR / "optional" / "vector.sql"


def connect() -> psycopg.Connection:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    # Autocommit: every multi-statement unit opens an explicit conn.transaction(),
    # which user_session relies on to scope SET LOCAL.
    return psycopg.connect(url, autocommit=True)


def init_schema(conn: psycopg.Connection, settings: Settings) -> None:
    """Apply sql/*.sql in name order, plus the pgvector schema if enabled. Idempotent."""
    with conn.transaction():
        for path in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        if settings.vector_enabled:
            sql = VECTOR_SQL.read_text(encoding="utf-8")
            conn.execute(sql.replace("{{EMBEDDING_DIM}}", str(int(settings.embedding_dim))))
            _check_vector_dim(conn, settings.embedding_dim)


def _check_vector_dim(conn: psycopg.Connection, dim: int) -> None:
    row = conn.execute(
        """
        SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'kb.chunks'::regclass AND attname = 'embedding' AND NOT attisdropped
        """
    ).fetchone()
    if row is None or row[0] != dim:
        raise RuntimeError(
            f"kb.chunks.embedding has dimension {row and row[0]}, but KB_EMBEDDING_DIM is {dim}; "
            "drop the embedding columns or change the model"
        )


def refresh_index(conn: psycopg.Connection) -> None:
    conn.execute("CALL kb.refresh_index()")


@contextmanager
def user_session(conn: psycopg.Connection, user_id: int) -> Iterator[psycopg.Connection]:
    """Run queries as role kb_app on behalf of user_id, so row-level security applies."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE kb_app")
        conn.execute("SELECT set_config('kb.user_id', %s, true)", (str(int(user_id)),))
        yield conn
        # Undo explicitly: when nested in an outer transaction, SET LOCAL would
        # otherwise outlive this block.
        conn.execute("RESET ROLE")
        conn.execute("SELECT set_config('kb.user_id', '', true)")
