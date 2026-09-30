"""Near-duplicate detection on upload, and the notifications it sends.

Detection runs over all documents (a duplicate in another team's space is
exactly what nobody would otherwise see), but each notification only names
documents its recipient is allowed to read.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import class_row

from kb.config import Settings
from kb.db import user_session
from kb.rbac import can_read


@dataclass
class SimilarDocument:
    doc_id: int | None  # None when the uploader may not read it
    title: str | None
    lexical_score: float | None
    vector_score: float | None


@dataclass
class Notification:
    id: int
    message: str
    created_at: datetime
    read_at: datetime | None
    alert_id: int | None
    new_doc_id: int | None  # only set when the recipient may read that document
    existing_doc_id: int | None


def lexical_neighbours(conn: psycopg.Connection, doc_id: int, threshold: float, limit: int = 5) -> dict[int, float]:
    """Jaccard similarity of the stemmed term sets."""
    rows = conn.execute(
        """
        WITH terms AS (
            SELECT DISTINCT c.doc_id, t.lexeme
            FROM kb.chunks c, unnest(c.tsv) AS t (lexeme, positions, weights)
        ),
        target AS (SELECT lexeme FROM terms WHERE doc_id = %(doc)s),
        sizes  AS (SELECT doc_id, count(*) AS n FROM terms GROUP BY doc_id),
        shared AS (
            SELECT t.doc_id, count(*) AS n
            FROM terms t JOIN target USING (lexeme)
            WHERE t.doc_id <> %(doc)s
            GROUP BY t.doc_id
        )
        SELECT s.doc_id, s.n::float8 / (a.n + b.n - s.n) AS jaccard
        FROM shared s
        JOIN sizes a ON a.doc_id = s.doc_id
        JOIN sizes b ON b.doc_id = %(doc)s
        ORDER BY jaccard DESC
        LIMIT %(limit)s
        """,
        {"doc": doc_id, "limit": limit},
    ).fetchall()
    return {other: score for other, score in rows if score >= threshold}


def vector_neighbours(conn: psycopg.Connection, doc_id: int, threshold: float, limit: int = 5) -> dict[int, float]:
    """Cosine similarity of the mean chunk embeddings."""
    rows = conn.execute(
        """
        SELECT d.id, 1 - (d.embedding <=> t.embedding) AS similarity
        FROM kb.documents t
        JOIN kb.documents d ON d.id <> t.id AND d.embedding IS NOT NULL
        WHERE t.id = %s AND t.embedding IS NOT NULL
        ORDER BY d.embedding <=> t.embedding
        LIMIT %s
        """,
        (doc_id, limit),
    ).fetchall()
    return {other: score for other, score in rows if score >= threshold}


def detect_and_notify(conn: psycopg.Connection, doc_id: int, settings: Settings) -> list[SimilarDocument]:
    """Record alerts for documents similar to doc_id and notify the people involved.

    Returns the matches as the uploader may see them.
    """
    lexical = lexical_neighbours(conn, doc_id, settings.duplicate_lexical_threshold)
    vector = vector_neighbours(conn, doc_id, settings.duplicate_vector_threshold) if settings.vector_enabled else {}
    uploader = conn.execute("SELECT uploaded_by FROM kb.documents WHERE id = %s", (doc_id,)).fetchone()[0]

    results = []
    with conn.transaction():
        for other in sorted(lexical.keys() | vector.keys(), key=lambda d: -max(lexical.get(d, 0), vector.get(d, 0))):
            alert_id, is_new = _upsert_alert(conn, doc_id, other, lexical.get(other), vector.get(other))
            if is_new:
                _notify(conn, alert_id, doc_id, other, lexical.get(other), vector.get(other))
            visible = uploader is not None and can_read(conn, uploader, other)
            title = conn.execute("SELECT title FROM kb.documents WHERE id = %s", (other,)).fetchone()[0]
            results.append(
                SimilarDocument(other if visible else None, title if visible else None, lexical.get(other), vector.get(other))
            )
    return results


def _upsert_alert(
    conn: psycopg.Connection, new_doc: int, existing_doc: int, lexical: float | None, vector: float | None
) -> tuple[int, bool]:
    # A pair is alerted once, whichever of the two was uploaded last.
    row = conn.execute(
        """
        SELECT id FROM kb.similarity_alerts
        WHERE (new_doc_id, existing_doc_id) IN ((%(a)s, %(b)s), (%(b)s, %(a)s))
        """,
        {"a": new_doc, "b": existing_doc},
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE kb.similarity_alerts SET lexical_score = %s, vector_score = %s WHERE id = %s",
            (lexical, vector, row[0]),
        )
        return row[0], False
    alert_id = conn.execute(
        """
        INSERT INTO kb.similarity_alerts (new_doc_id, existing_doc_id, lexical_score, vector_score)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (new_doc, existing_doc, lexical, vector),
    ).fetchone()[0]
    return alert_id, True


def _notify(
    conn: psycopg.Connection, alert_id: int, new_doc: int, existing_doc: int, lexical: float | None, vector: float | None
) -> None:
    docs = dict(
        (doc_id, (title, uploaded_by))
        for doc_id, title, uploaded_by in conn.execute(
            "SELECT id, title, uploaded_by FROM kb.documents WHERE id = ANY(%s)", ([new_doc, existing_doc],)
        )
    )
    uploader = docs[new_doc][1]
    managers = {
        uid
        for (uid,) in conn.execute(
            """
            SELECT DISTINCT gm.user_id
            FROM kb.document_groups dg
            JOIN kb.group_members gm ON gm.group_id = dg.group_id
            WHERE dg.doc_id = ANY(%s) AND gm.role = 'manager'
            """,
            ([new_doc, existing_doc],),
        )
    }
    if not managers:
        managers = {uid for (uid,) in conn.execute("SELECT id FROM kb.users WHERE is_admin")}
    recipients = (managers | {uploader, docs[existing_doc][1]}) - {None}

    scores = ", ".join(
        part
        for part in (
            f"text overlap {lexical:.0%}" if lexical is not None else None,
            f"meaning {vector:.0%}" if vector is not None else None,
        )
        if part
    )

    def name(uid: int, doc_id: int) -> str:
        return f"'{docs[doc_id][0]}'" if can_read(conn, uid, doc_id) else "a document you don't have access to"

    with conn.cursor() as cur:
        for uid in recipients:
            if uid == uploader:
                message = (
                    f"Your upload {name(uid, new_doc)} looks similar to {name(uid, existing_doc)} ({scores}). "
                    "Check whether it duplicates, updates or contradicts it."
                )
            else:
                message = (
                    f"New upload {name(uid, new_doc)} looks similar to {name(uid, existing_doc)} ({scores}). "
                    "Please review it for duplicated or conflicting information."
                )
            cur.execute(
                "INSERT INTO kb.notifications (user_id, alert_id, message) VALUES (%s, %s, %s)",
                (uid, alert_id, message),
            )


def list_notifications(conn: psycopg.Connection, user_id: int, unread_only: bool = False) -> list[Notification]:
    with user_session(conn, user_id), conn.cursor(row_factory=class_row(Notification)) as cur:
        cur.execute(
            """
            SELECT n.id, n.message, n.created_at, n.read_at, n.alert_id,
                   CASE WHEN a.new_doc_id IN (SELECT kb.my_document_ids()) THEN a.new_doc_id END AS new_doc_id,
                   CASE WHEN a.existing_doc_id IN (SELECT kb.my_document_ids()) THEN a.existing_doc_id END
                       AS existing_doc_id
            FROM kb.notifications n
            LEFT JOIN kb.similarity_alerts a ON a.id = n.alert_id
            WHERE n.user_id = kb.current_user_id() AND (NOT %s OR n.read_at IS NULL)
            ORDER BY n.created_at DESC, n.id DESC
            LIMIT 100
            """,
            (unread_only,),
        )
        return cur.fetchall()


def mark_read(conn: psycopg.Connection, user_id: int, notification_id: int) -> bool:
    """Returns False if the notification does not exist or belongs to someone else."""
    with user_session(conn, user_id):
        row = conn.execute(
            """
            UPDATE kb.notifications SET read_at = coalesce(read_at, now())
            WHERE id = %s AND user_id = kb.current_user_id()
            RETURNING id
            """,
            (notification_id,),
        ).fetchone()
    return row is not None
