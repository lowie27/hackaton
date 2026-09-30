"""Load markdown/text files into kb.documents and kb.chunks."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg

from kb.db import refresh_index

SUPPORTED_SUFFIXES = {".md", ".txt"}
METADATA_KEYS = {"title", "source", "owner", "country", "updated_at"}


@dataclass
class Document:
    external_id: str
    title: str
    body: str
    meta: dict[str, str] = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a simple `key: value` frontmatter block (between --- lines) from the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            meta = {}
            for entry in lines[1:end]:
                key, sep, value = entry.partition(":")
                if sep and key.strip() in METADATA_KEYS:
                    meta[key.strip()] = value.strip()
            return meta, "\n".join(lines[end + 1 :])
    return {}, text


def chunk_text(text: str, max_words: int = 200) -> list[str]:
    """Pack paragraphs into chunks of at most ~max_words words.

    A single paragraph longer than max_words is split on word boundaries.
    """
    chunks: list[str] = []
    current: list[str] = []
    count = 0

    def flush() -> None:
        nonlocal current, count
        if current:
            chunks.append("\n\n".join(current))
        current, count = [], 0

    for para in (p.strip() for p in text.split("\n\n")):
        words = para.split()
        if not words:
            continue
        if len(words) > max_words:
            flush()
            for start in range(0, len(words), max_words):
                chunks.append(" ".join(words[start : start + max_words]))
            continue
        if count + len(words) > max_words:
            flush()
        current.append(para)
        count += len(words)
    flush()
    return chunks


def load_directory(root: Path) -> list[Document]:
    docs = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        title = meta.pop("title", path.stem.replace("_", " "))
        docs.append(Document(str(path.relative_to(root)), title, body, meta))
    return docs


def upsert_document(conn: psycopg.Connection, doc: Document) -> int:
    updated_at = doc.meta.get("updated_at")
    row = conn.execute(
        """
        INSERT INTO kb.documents (external_id, title, source, owner, country, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            title = EXCLUDED.title,
            source = EXCLUDED.source,
            owner = EXCLUDED.owner,
            country = EXCLUDED.country,
            updated_at = EXCLUDED.updated_at,
            ingested_at = now()
        RETURNING id
        """,
        (
            doc.external_id,
            doc.title,
            doc.meta.get("source"),
            doc.meta.get("owner"),
            doc.meta.get("country"),
            date.fromisoformat(updated_at) if updated_at else None,
        ),
    ).fetchone()
    doc_id = row[0]

    conn.execute("DELETE FROM kb.chunks WHERE doc_id = %s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO kb.chunks (doc_id, ord, heading, text) VALUES (%s, %s, %s, %s)",
            [(doc_id, i, doc.title, chunk) for i, chunk in enumerate(chunk_text(doc.body))],
        )
    return doc_id


def ingest_directory(conn: psycopg.Connection, root: Path) -> int:
    docs = load_directory(root)
    with conn.transaction():
        for doc in docs:
            upsert_document(conn, doc)
    refresh_index(conn)
    return len(docs)
