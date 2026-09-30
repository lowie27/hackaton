"""Python-side retrieval: calls kb.bm25_search and attaches document metadata."""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import class_row


@dataclass
class SearchHit:
    score: float
    matched_terms: list[str]
    chunk_id: int
    chunk_ord: int
    text: str
    external_id: str
    title: str
    source: str | None
    owner: str | None
    country: str | None
    updated_at: datetime | None


class BM25Retriever:
    def __init__(self, conn: psycopg.Connection, k1: float = 1.2, b: float = 0.75):
        self.conn = conn
        self.k1 = k1
        self.b = b

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        if not query.strip():
            return []
        top_k = max(1, min(top_k, 100))
        with self.conn.cursor(row_factory=class_row(SearchHit)) as cur:
            cur.execute(
                """
                SELECT r.score, r.matched_terms,
                       c.id AS chunk_id, c.ord AS chunk_ord, c.text,
                       d.external_id, d.title, d.source, d.owner, d.country, d.updated_at
                FROM kb.bm25_search(%s, %s, %s, %s) r
                JOIN kb.chunks c    ON c.id = r.chunk_id
                JOIN kb.documents d ON d.id = c.doc_id
                ORDER BY r.score DESC, c.id
                """,
                (query, top_k, self.k1, self.b),
            )
            return cur.fetchall()
