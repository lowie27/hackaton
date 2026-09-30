"""Hybrid retrieval: BM25 (in Postgres) + pgvector, fused with reciprocal rank fusion,
optionally reranked by a cross-encoder, then re-ranked by context (kb.context).

All queries run inside user_session, so row-level security limits results to
documents the user may read.
"""

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from kb.config import Settings
from kb.conflicts import annotate
from kb.context import CONTRADICTED, MIN_FACTOR
from kb.context import SIGNALS, UserContext, assess
from kb.db import user_session
from kb.embeddings import Embedder, Reranker, to_pgvector

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
    department: str | None = None
    location: str | None = None
    language: str | None = None
    tags: list[str] = field(default_factory=list)
    valid_from: date | None = None
    valid_until: date | None = None
    uploader_position: str | None = None
    uploader_department: str | None = None
    uploader_is_manager: bool = False
    rerank_score: float | None = None  # cross-encoder relevance, 0..1
    rerank_rank: int | None = None
    relevance: float | None = None  # score before context ranking
    trust: float = 1.0  # context factor: score = relevance * trust
    reasons: list[str] = field(default_factory=list)  # why it applies / can be trusted
    warnings: list[str] = field(default_factory=list)  # why to be careful
    disagreements: list[dict] = field(default_factory=list)  # facts where other results say otherwise (kb.conflicts)


@dataclass
class SearchFilters:
    """Hard filters, applied in SQL before top_k. Empty fields do not filter."""

    country: str | None = None  # also keeps documents that apply to all countries
    department: str | None = None  # also keeps documents for all departments
    source: str | None = None
    language: str | None = None
    tags: Sequence[str] = ()  # document must carry all of them
    valid_on: date | None = None  # drop documents not in force on that date

    def where(self) -> tuple[sql.Composable, dict]:
        """SQL condition on alias d (kb.documents). Only fixed SQL text; values are always bound parameters."""
        clauses, params = ["true"], {}
        if self.country:
            clauses.append("(d.country IS NULL OR upper(d.country) = %(f_country)s)")
            params["f_country"] = self.country.strip().upper()
        if self.department:
            clauses.append("(d.department IS NULL OR lower(d.department) = %(f_department)s)")
            params["f_department"] = self.department.strip().lower()
        if self.source:
            clauses.append("lower(d.source) = %(f_source)s")
            params["f_source"] = self.source.strip().lower()
        if self.language:
            clauses.append("d.language = %(f_language)s")
            params["f_language"] = self.language.strip().lower()
        if self.tags:
            clauses.append("d.tags @> %(f_tags)s")
            params["f_tags"] = [t.strip().lower() for t in self.tags]
        if self.valid_on:
            clauses.append(
                "(d.valid_from IS NULL OR d.valid_from <= %(f_valid_on)s)"
                " AND (d.valid_until IS NULL OR d.valid_until >= %(f_valid_on)s)"
            )
            params["f_valid_on"] = self.valid_on
        return sql.SQL(" AND ").join(sql.SQL(c) for c in clauses), params

    @property
    def active(self) -> bool:
        return any((self.country, self.department, self.source, self.language, self.tags, self.valid_on))


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(
        self,
        conn: psycopg.Connection,
        settings: Settings,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ):
        if settings.vector_enabled and embedder is None:
            raise ValueError("vector search is enabled but no embedder was given")
        self.conn = conn
        self.settings = settings
        self.embedder = embedder
        self.reranker = reranker

    def search(
        self,
        user_id: int,
        query: str,
        top_k: int = 5,
        mode: SearchMode | None = None,
        filters: SearchFilters | None = None,
        today: date | None = None,
        context_ranking: bool | None = None,
        signals: Collection[str] = SIGNALS,
        rerank: bool | None = None,
        detect_conflicts: bool = True,
    ) -> list[SearchHit]:
        """context_ranking and rerank override the settings per request; signals picks which context signals count.

        Pipeline: RLS + filters -> BM25 / vector -> fusion -> cross-encoder rerank -> context ranking.
        """
        use_context = self.settings.context_ranking if context_ranking is None else context_ranking
        use_rerank = (self.reranker is not None) if rerank is None else rerank
        if use_rerank and self.reranker is None:
            raise ValueError("reranking is disabled (set KB_RERANK_ENABLED=true)")
        mode = mode or ("hybrid" if self.settings.vector_enabled else "bm25")
        if mode not in ("bm25", "vector", "hybrid"):
            raise ValueError(f"unknown search mode: {mode}")
        if mode != "bm25" and not self.settings.vector_enabled:
            raise ValueError("vector search is disabled (set KB_VECTOR_ENABLED=true)")
        if not query.strip():
            return []
        filters = filters or SearchFilters()
        today = today or date.today()
        top_k = max(1, min(top_k, 50))
        pool = min(max(top_k * 4, 20), 100)
        # Embed before opening the transaction: model inference is the slow part.
        query_vec = to_pgvector(self.embedder.embed_query(query)) if mode != "bm25" else None
        user = self._user_context(user_id)

        with user_session(self.conn, user_id):
            bm25 = self._bm25(query, pool, filters) if mode != "vector" else []
            vector = self._vector(query_vec, pool, filters) if query_vec else []

            bm25_by_id = {cid: (rank, score, terms) for rank, (cid, score, terms) in enumerate(bm25, start=1)}
            vector_by_id = {cid: (rank, score) for rank, (cid, score) in enumerate(vector, start=1)}
            if mode == "hybrid":
                fused = reciprocal_rank_fusion([[c for c, *_ in bm25], [c for c, _ in vector]], self.settings.rrf_k)
            elif mode == "bm25":
                fused = {cid: score for cid, score, _ in bm25}
            else:
                fused = {cid: score for cid, score in vector}
            # Context ranking looks at the whole candidate pool, so a relevant
            # document for the user's country can overtake one for another country.
            candidates = sorted(fused, key=lambda cid: (-fused[cid], cid))
            if use_rerank:
                candidates = candidates[: max(self.settings.rerank_candidates, top_k)]
            elif not use_context:
                candidates = candidates[:top_k]
            rows = self._load(candidates)

        # Outside the transaction: model inference is the slow part.
        reranked: dict[int, tuple[int, float]] = {}
        if use_rerank and candidates:
            logits = self.reranker.score(query, [f"{rows[c]['title']}\n\n{rows[c]['text']}" for c in candidates])
            probs = {cid: 1 / (1 + math.exp(-x)) for cid, x in zip(candidates, logits)}
            candidates = sorted(candidates, key=lambda cid: (-probs[cid], cid))
            reranked = {cid: (rank, probs[cid]) for rank, cid in enumerate(candidates, start=1)}
            fused = probs  # the reranker's judgement becomes the relevance score

        hits = []
        for cid in candidates:
            bm25_rank, bm25_score, terms = bm25_by_id.get(cid, (None, None, []))
            vector_rank, vector_score = vector_by_id.get(cid, (None, None))
            rerank_rank, rerank_score = reranked.get(cid, (None, None))
            row = rows[cid]
            score = fused[cid]
            reasons, warnings, trust = [], [], 1.0
            if use_context:
                assessment = assess(row, user, today, signals)
                trust = assessment.factor
                score *= trust
                reasons, warnings = assessment.reasons, assessment.warnings
            hits.append(
                SearchHit(
                    score=score,
                    bm25_score=bm25_score,
                    bm25_rank=bm25_rank,
                    vector_score=vector_score,
                    vector_rank=vector_rank,
                    matched_terms=terms,
                    rerank_score=rerank_score,
                    rerank_rank=rerank_rank,
                    relevance=fused[cid],
                    reasons=reasons,
                    warnings=warnings,
                    trust=trust,
                    **row,
                )
            )
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        if detect_conflicts and use_context and "conflicts" in signals:
            self._demote_contradicted(hits, query)
        hits = hits[:top_k]
        if detect_conflicts:
            annotate(hits, query)
        return hits

    @staticmethod
    def _demote_contradicted(hits: list[SearchHit], query: str) -> None:
        """A result that contradicts a more trustworthy one loses trust, with the reason spelled out."""
        annotate(hits, query)
        for hit in hits:
            against = [d for d in hit.disagreements if not d["most_trusted"]]
            if against:
                trust = max(MIN_FACTOR, hit.trust + CONTRADICTED)
                hit.score = hit.score / hit.trust * trust
                hit.trust = trust
                d = against[0]
                hit.warnings.append(f"contradicts a more trustworthy source on the {d['label']} "
                                    f"({d['value']} vs {d['other_value']})")
        for hit in hits:
            hit.disagreements = []
        hits.sort(key=lambda h: (-h.score, h.chunk_id))

    def _user_context(self, user_id: int) -> UserContext:
        # Trusted read on the owner connection: the profile of the authenticated user only.
        row = self.conn.execute(
            "SELECT country, location, department, position FROM kb.users WHERE id = %s", (user_id,)
        ).fetchone()
        return UserContext(*row) if row else UserContext()

    def _bm25(self, query: str, limit: int, filters: SearchFilters) -> list[tuple[int, float, list[str]]]:
        where, params = filters.where()
        # With filters, ask bm25_search for more so filtered-out chunks do not use up the limit.
        return self.conn.execute(
            sql.SQL("""
            SELECT b.chunk_id, b.score, b.matched_terms
            FROM kb.bm25_search(%(q)s, %(inner)s) b
            JOIN kb.chunks c ON c.id = b.chunk_id
            JOIN kb.documents d ON d.id = c.doc_id
            WHERE {where}
            ORDER BY b.score DESC, b.chunk_id
            LIMIT %(limit)s
            """).format(where=where),
            {"q": query, "inner": 5000 if filters.active else limit, "limit": limit, **params},
        ).fetchall()

    def _vector(self, query_vec: str, limit: int, filters: SearchFilters) -> list[tuple[int, float]]:
        where, params = filters.where()
        # Without iterative scans, HNSW returns ~40 candidates and RLS may filter
        # all of them away; relaxed_order keeps scanning until LIMIT is met.
        self.conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = self.conn.execute(
            sql.SQL("""
            SELECT c.id, 1 - (c.embedding <=> %(q)s::vector) AS similarity
            FROM kb.chunks c
            JOIN kb.documents d ON d.id = c.doc_id
            WHERE c.embedding IS NOT NULL AND {where}
            ORDER BY c.embedding <=> %(q)s::vector
            LIMIT %(limit)s
            """).format(where=where),
            {"q": query_vec, "limit": limit, **params},
        ).fetchall()
        rows = [(cid, sim) for cid, sim in rows if sim >= self.settings.min_vector_similarity]
        return sorted(rows, key=lambda r: (-r[1], r[0]))  # relaxed_order may be slightly out of order

    def _load(self, chunk_ids: list[int]) -> dict[int, dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.ord AS chunk_ord, c.text,
                       d.id AS doc_id, d.external_id, d.title, d.source, d.owner, d.country, d.updated_at,
                       d.department, d.location, d.language, d.tags, d.valid_from, d.valid_until,
                       d.uploader_position, d.uploader_department, d.uploader_is_manager,
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
