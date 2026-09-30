"""Parse and chunk files, and write documents to kb.documents / kb.chunks.

No permission checks here: callers go through kb.service.KnowledgeBase.upload.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg

from kb.embeddings import to_pgvector

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


def load_file(path: Path, root: Path) -> Document:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    title = meta.pop("title", path.stem.replace("_", " "))
    return Document(str(path.relative_to(root)), title, body, meta)


def load_directory(root: Path) -> list[Document]:
    if root.is_file():
        return [load_file(root, root.parent)]
    return [
        load_file(path, root)
        for path in sorted(root.rglob("*"))
        if path.suffix.lower() in SUPPORTED_SUFFIXES and path.is_file()
    ]


def find_document_id(conn: psycopg.Connection, external_id: str) -> int | None:
    row = conn.execute("SELECT id FROM kb.documents WHERE external_id = %s", (external_id,)).fetchone()
    return row[0] if row else None


def write_document(
    conn: psycopg.Connection,
    doc: Document,
    chunks: Sequence[str],
    uploader_id: int,
    group_ids: Sequence[int],
    embeddings: Sequence[Sequence[float]] | None = None,
) -> int:
    """Insert or replace a document, its group shares and its chunks. Call inside a transaction."""
    updated_at = doc.meta.get("updated_at")
    doc_id = conn.execute(
        """
        INSERT INTO kb.documents (external_id, title, source, owner, country, updated_at, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
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
            uploader_id,
        ),
    ).fetchone()[0]

    conn.execute("DELETE FROM kb.document_groups WHERE doc_id = %s", (doc_id,))
    conn.execute("DELETE FROM kb.chunks WHERE doc_id = %s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO kb.document_groups (doc_id, group_id) VALUES (%s, %s)",
            [(doc_id, gid) for gid in set(group_ids)],
        )
        if embeddings is None:
            cur.executemany(
                "INSERT INTO kb.chunks (doc_id, ord, heading, text) VALUES (%s, %s, %s, %s)",
                [(doc_id, i, doc.title, chunk) for i, chunk in enumerate(chunks)],
            )
        else:
            cur.executemany(
                "INSERT INTO kb.chunks (doc_id, ord, heading, text, embedding) VALUES (%s, %s, %s, %s, %s::vector)",
                [(doc_id, i, doc.title, chunk, to_pgvector(vec)) for i, (chunk, vec) in enumerate(zip(chunks, embeddings))],
            )
            update_document_embeddings(conn, [doc_id])
    return doc_id


def update_document_embeddings(conn: psycopg.Connection, doc_ids: Sequence[int]) -> None:
    conn.execute(
        """
        UPDATE kb.documents d
        SET embedding = (SELECT avg(c.embedding) FROM kb.chunks c WHERE c.doc_id = d.id)
        WHERE d.id = ANY(%s)
        """,
        (list(doc_ids),),
    )
