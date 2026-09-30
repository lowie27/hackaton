"""Hybrid retrieval: BM25 (in Postgres) + pgvector, fused with reciprocal rank fusion.

All queries run inside user_session, so row-level security limits results to
documents the user may read.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg
from psycopg.rows import dict_row

from kb.config import Settings
from kb.db import user_session
from kb.embeddings import Embedder, to_pgvector

SearchMode = Literal["bm25", "vector", "hybrid"]


@dataclass
class SearchHit:
    score: float  # RRF score in hybrid mode, otherwise the single method's score
    bm25_score: float | None
    bm25_rank: int | None
    vector_score: float | None  # cosine similarity
    vector_rank: int | None
    matched_terms: list[str]
    chunk_id: int
    chunk_ord: int
    text: str
    doc_id: int
    external_id: str
    title: str
    source: str | None
    owner: str | None
    country: str | None
    updated_at: datetime | None
    groups: list[str]


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(self, conn: psycopg.Connection, settings: Settings, embedder: Embedder | None = None):
        if settings.vector_enabled and embedder is None:
            raise ValueError("vector search is enabled but no embedder was given")
        self.conn = conn
        self.settings = settings
        self.embedder = embedder

    def search(self, user_id: int, query: str, top_k: int = 5, mode: SearchMode | None = None) -> list[SearchHit]:
        mode = mode or ("hybrid" if self.settings.vector_enabled else "bm25")
        if mode not in ("bm25", "vector", "hybrid"):
            raise ValueError(f"unknown search mode: {mode}")
        if mode != "bm25" and not self.settings.vector_enabled:
            raise ValueError("vector search is disabled (set KB_VECTOR_ENABLED=true)")
        if not query.strip():
            return []
        top_k = max(1, min(top_k, 50))
        pool = min(max(top_k * 4, 20), 100)
        # Embed before opening the transaction: model inference is the slow part.
        query_vec = to_pgvector(self.embedder.embed_query(query)) if mode != "bm25" else None

        with user_session(self.conn, user_id):
            bm25 = self._bm25(query, pool) if mode != "vector" else []
            vector = self._vector(query_vec, pool) if query_vec else []

            bm25_by_id = {cid: (rank, score, terms) for rank, (cid, score, terms) in enumerate(bm25, start=1)}
            vector_by_id = {cid: (rank, score) for rank, (cid, score) in enumerate(vector, start=1)}
            if mode == "hybrid":
                fused = reciprocal_rank_fusion([[c for c, *_ in bm25], [c for c, _ in vector]], self.settings.rrf_k)
            elif mode == "bm25":
                fused = {cid: score for cid, score, _ in bm25}
            else:
                fused = {cid: score for cid, score in vector}
            ranked = sorted(fused, key=lambda cid: (-fused[cid], cid))[:top_k]
            rows = self._load(ranked)

        hits = []
        for cid in ranked:
            bm25_rank, bm25_score, terms = bm25_by_id.get(cid, (None, None, []))
            vector_rank, vector_score = vector_by_id.get(cid, (None, None))
            hits.append(
                SearchHit(
                    score=fused[cid],
                    bm25_score=bm25_score,
                    bm25_rank=bm25_rank,
                    vector_score=vector_score,
                    vector_rank=vector_rank,
                    matched_terms=terms,
                    **rows[cid],
                )
            )
        return hits

    def _bm25(self, query: str, limit: int) -> list[tuple[int, float, list[str]]]:
        return self.conn.execute(
            "SELECT chunk_id, score, matched_terms FROM kb.bm25_search(%s, %s)", (query, limit)
        ).fetchall()

    def _vector(self, query_vec: str, limit: int) -> list[tuple[int, float]]:
        # Without iterative scans, HNSW returns ~40 candidates and RLS may filter
        # all of them away; relaxed_order keeps scanning until LIMIT is met.
        self.conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = self.conn.execute(
            """
            SELECT c.id, 1 - (c.embedding <=> %(q)s::vector) AS similarity
            FROM kb.chunks c
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> %(q)s::vector
            LIMIT %(limit)s
            """,
            {"q": query_vec, "limit": limit},
        ).fetchall()
        rows = [(cid, sim) for cid, sim in rows if sim >= self.settings.min_vector_similarity]
        return sorted(rows, key=lambda r: (-r[1], r[0]))  # relaxed_order may be slightly out of order

    def _load(self, chunk_ids: list[int]) -> dict[int, dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.ord AS chunk_ord, c.text,
                       d.id AS doc_id, d.external_id, d.title, d.source, d.owner, d.country, d.updated_at,
                       array(
                           SELECT g.name FROM kb.document_groups dg JOIN kb.groups g ON g.id = dg.group_id
                           WHERE dg.doc_id = d.id ORDER BY g.name
                       ) AS groups
                FROM kb.chunks c
                JOIN kb.documents d ON d.id = c.doc_id
                WHERE c.id = ANY(%s)
                """,
                (chunk_ids,),
            )
            return {row["chunk_id"]: row for row in cur}
