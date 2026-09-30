"""KnowledgeBase: the entry point for the UI/API layer.

Every method takes the acting user's id. The API layer is responsible for
authenticating that user; everything below enforces what they may do.
"""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from kb import rbac
from kb.alerts import Notification, SimilarDocument, detect_and_notify, list_notifications, mark_read
from kb.config import Settings
from kb.context import SIGNALS
from kb.db import refresh_index
from kb.embeddings import Embedder, FastEmbedEmbedder, FastEmbedReranker, Reranker, to_pgvector
from kb.ingest import Document, chunk_text, find_document_id, load_directory, load_file, update_document_embeddings, write_document
from kb.retriever import Retriever, SearchFilters, SearchHit, SearchMode


@dataclass
class UploadResult:
    doc_id: int
    similar: list[SimilarDocument]


class KnowledgeBase:
    def __init__(
        self,
        conn: psycopg.Connection,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ):
        self.conn = conn
        self.settings = settings or Settings.from_env()
        if self.settings.vector_enabled and embedder is None:
            embedder = FastEmbedEmbedder(self.settings.embedding_model, self.settings.embedding_dim)
        self.embedder = embedder if self.settings.vector_enabled else None
        if self.settings.rerank_enabled and reranker is None:
            reranker = FastEmbedReranker(self.settings.rerank_model)
        self.retriever = Retriever(conn, self.settings, self.embedder, reranker)

    def search(
        self,
        user_id: int,
        query: str,
        top_k: int = 5,
        mode: SearchMode | None = None,
        filters: SearchFilters | None = None,
        context_ranking: bool | None = None,
        signals: Collection[str] = SIGNALS,
        rerank: bool | None = None,
        detect_conflicts: bool = True,
    ) -> list[SearchHit]:
        return self.retriever.search(
            user_id, query, top_k, mode, filters,
            context_ranking=context_ranking, signals=signals, rerank=rerank, detect_conflicts=detect_conflicts,
        )

    def upload(
        self,
        user_id: int,
        doc: Document,
        group_names: Sequence[str] = (),
        refresh: bool = True,
        detect_duplicates: bool = True,
    ) -> UploadResult:
        """Store a document shared with group_names, then check it for near-duplicates.

        Raises rbac.PermissionDenied if the user may not share with those groups
        or may not overwrite an existing document with the same external_id.
        """
        rbac.require_user(self.conn, user_id)
        group_ids = rbac.resolve_groups(self.conn, group_names)
        rbac.check_can_share_with(self.conn, user_id, group_ids)
        rbac.check_can_claim_authority(self.conn, user_id, group_ids, doc.meta)
        existing = find_document_id(self.conn, doc.external_id)
        if existing is not None:
            rbac.check_can_replace(self.conn, user_id, existing)

        chunks = chunk_text(doc.body)
        embeddings = None
        if self.embedder and chunks:
            embeddings = self.embedder.embed_documents([f"{doc.title}\n\n{chunk}" for chunk in chunks])

        with self.conn.transaction():
            doc_id = write_document(self.conn, doc, chunks, user_id, group_ids, embeddings)
        if refresh:
            refresh_index(self.conn)
        similar = detect_and_notify(self.conn, doc_id, self.settings) if detect_duplicates else []
        return UploadResult(doc_id, similar)

    def upload_directory(self, user_id: int, root: Path, group_names: Sequence[str] = ()) -> list[UploadResult]:
        results = [self.upload(user_id, doc, group_names, refresh=False) for doc in load_directory(root)]
        refresh_index(self.conn)
        return results

    def notifications(self, user_id: int, unread_only: bool = False) -> list[Notification]:
        return list_notifications(self.conn, user_id, unread_only)

    def mark_notification_read(self, user_id: int, notification_id: int) -> bool:
        return mark_read(self.conn, user_id, notification_id)

    def embed_missing(self, batch_size: int = 64) -> int:
        """Backfill embeddings for chunks stored while vector search was off. Admin task."""
        if not self.embedder:
            raise ValueError("vector search is disabled (set KB_VECTOR_ENABLED=true)")
        rows = self.conn.execute(
            "SELECT id, doc_id, heading, text FROM kb.chunks WHERE embedding IS NULL ORDER BY id"
        ).fetchall()
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            vectors = self.embedder.embed_documents([f"{heading}\n\n{text}" for _, _, heading, text in batch])
            with self.conn.transaction(), self.conn.cursor() as cur:
                cur.executemany(
                    "UPDATE kb.chunks SET embedding = %s::vector WHERE id = %s",
                    [(to_pgvector(vec), cid) for (cid, *_), vec in zip(batch, vectors)],
                )
        if rows:
            update_document_embeddings(self.conn, sorted({doc_id for _, doc_id, _, _ in rows}))
        return len(rows)

    def seed(self, spec_path: Path) -> None:
        """Create demo users, groups and documents from a JSON spec (see data/sample/seed.json)."""
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        with self.conn.transaction():
            users = {
                u["email"]: rbac.upsert_user(
                    self.conn,
                    u["email"],
                    u["name"],
                    u.get("admin", False),
                    country=u.get("country"),
                    location=u.get("location"),
                    department=u.get("department"),
                    position=u.get("position"),
                )
                for u in spec["users"]
            }
            for name, group in spec["groups"].items():
                group_id = rbac.upsert_group(self.conn, name)
                for email in group.get("members", []):
                    rbac.add_member(self.conn, group_id, users[email], "member")
                for email in group.get("managers", []):
                    rbac.add_member(self.conn, group_id, users[email], "manager")
        root = spec_path.parent
        for entry in spec["documents"]:
            doc = load_file(root / entry["file"], root)
            self.upload(users[entry["uploader"]], doc, entry.get("groups", []), refresh=False)
        refresh_index(self.conn)
