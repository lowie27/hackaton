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
METADATA_KEYS = {
    "title", "source", "owner", "country", "updated_at",
    "department", "location", "language", "tags", "valid_from", "valid_until",
}


@dataclass
class Document:
    external_id: str
    title: str
    body: str
    meta: dict[str, str | list[str]] = field(default_factory=dict)


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


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value.strip()) if value and value.strip() else None


def _tags(value: str | Sequence[str] | None) -> list[str]:
    """Frontmatter gives "a, b"; the API may pass a list. Lowercased, deduplicated, order kept."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    return list(dict.fromkeys(t.strip().lower() for t in items if t.strip()))


def _upper(value: str | None) -> str | None:
    return value.strip().upper() if value and value.strip() else None


def write_document(
    conn: psycopg.Connection,
    doc: Document,
    chunks: Sequence[str],
    uploader_id: int,
    group_ids: Sequence[int],
    embeddings: Sequence[Sequence[float]] | None = None,
) -> int:
    """Insert or replace a document, its group shares and its chunks. Call inside a transaction."""
    meta = doc.meta
    valid_from, valid_until = _date(meta.get("valid_from")), _date(meta.get("valid_until"))
    if valid_from and valid_until and valid_until < valid_from:
        raise ValueError("valid_until is before valid_from")
    # The uploader's position and department come from their profile, never from
    # the document, so an upload cannot claim someone else's authority.
    doc_id = conn.execute(
        """
        INSERT INTO kb.documents (
            external_id, title, source, owner, country, updated_at, uploaded_by,
            department, location, language, tags, valid_from, valid_until,
            uploader_position, uploader_department, uploader_is_manager
        )
        SELECT %(external_id)s, %(title)s, %(source)s, %(owner)s, %(country)s, %(updated_at)s, u.id,
               %(department)s, %(location)s, %(language)s, %(tags)s, %(valid_from)s, %(valid_until)s,
               u.position, u.department,
               EXISTS (
                   SELECT 1 FROM kb.group_members gm
                   WHERE gm.user_id = u.id AND gm.role = 'manager' AND gm.group_id = ANY(%(groups)s)
               )
        FROM kb.users u WHERE u.id = %(uploader)s
        ON CONFLICT (external_id) DO UPDATE SET
            title = EXCLUDED.title,
            source = EXCLUDED.source,
            owner = EXCLUDED.owner,
            country = EXCLUDED.country,
            updated_at = EXCLUDED.updated_at,
            department = EXCLUDED.department,
            location = EXCLUDED.location,
            language = EXCLUDED.language,
            tags = EXCLUDED.tags,
            valid_from = EXCLUDED.valid_from,
            valid_until = EXCLUDED.valid_until,
            ingested_at = now()
        RETURNING id
        """,
        {
            "external_id": doc.external_id,
            "title": doc.title,
            "source": meta.get("source"),
            "owner": meta.get("owner"),
            "country": _upper(meta.get("country")),
            "updated_at": _date(meta.get("updated_at")),
            "uploader": uploader_id,
            "department": meta.get("department"),
            "location": meta.get("location"),
            "language": (meta.get("language") or "").strip().lower() or None,
            "tags": _tags(meta.get("tags")),
            "valid_from": valid_from,
            "valid_until": valid_until,
            "groups": list(set(group_ids)),
        },
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
