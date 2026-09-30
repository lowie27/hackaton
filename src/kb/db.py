"""Database connection and schema setup."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def connect() -> psycopg.Connection:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    return psycopg.connect(url)


def init_schema(conn: psycopg.Connection) -> None:
    """Apply every sql/*.sql file in name order. The files are idempotent."""
    for path in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def refresh_index(conn: psycopg.Connection) -> None:
    conn.execute("CALL kb.refresh_index()")
    conn.commit()
