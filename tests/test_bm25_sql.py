"""Checks kb.bm25_search against a plain-Python BM25 over the same inverted index.

Needs a running database (DATABASE_URL). Everything runs in a transaction that
is rolled back, so existing data is left alone.
"""

import math
import os

import pytest
from dotenv import load_dotenv

from kb.db import connect, init_schema

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

DOCS = {
    "holiday-be": "Holiday pay in Belgium is paid in May. Double holiday pay is calculated on gross salary.",
    "holiday-nl": "In the Netherlands holiday allowance is eight percent of gross salary, paid in May.",
    "expenses": "Expense claims must be submitted within thirty days with a receipt.",
}


def python_bm25(conn, query, k1=1.2, b=0.75):
    terms = [r[0] for r in conn.execute("SELECT DISTINCT lexeme FROM unnest(to_tsvector('english', %s))", (query,))]
    tf = {}
    for chunk_id, term, freq in conn.execute("SELECT chunk_id, term, tf FROM kb.chunk_terms"):
        tf.setdefault(chunk_id, {})[term] = freq
    n = len(conn.execute("SELECT id FROM kb.chunks").fetchall())
    lengths = {cid: sum(t.values()) for cid, t in tf.items()}
    avg_len = sum(lengths.values()) / n
    scores = {}
    for term in terms:
        df = sum(1 for t in tf.values() if term in t)
        if not df:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for cid, t in tf.items():
            if term in t:
                f = t[term]
                scores[cid] = scores.get(cid, 0) + idf * f * (k1 + 1) / (f + k1 * (1 - b + b * lengths[cid] / avg_len))
    return scores


@pytest.fixture
def conn():
    with connect() as c:
        init_schema(c)
        yield c
        c.rollback()


def test_sql_matches_python_reference(conn):
    for ext_id, body in DOCS.items():
        doc_id = conn.execute(
            "INSERT INTO kb.documents (external_id, title) VALUES (%s, %s) RETURNING id", (f"test/{ext_id}", ext_id)
        ).fetchone()[0]
        conn.execute("INSERT INTO kb.chunks (doc_id, ord, text) VALUES (%s, 0, %s)", (doc_id, body))
    conn.execute("CALL kb.refresh_index()")

    query = "double holiday pay Belgium"
    expected = python_bm25(conn, query)
    rows = conn.execute("SELECT chunk_id, score FROM kb.bm25_search(%s, 1000)", (query,)).fetchall()

    assert {cid for cid, _ in rows} == set(expected)
    for chunk_id, score in rows:
        assert score == pytest.approx(expected[chunk_id])
    top_title = conn.execute(
        "SELECT d.title FROM kb.chunks c JOIN kb.documents d ON d.id = c.doc_id WHERE c.id = %s", (rows[0][0],)
    ).fetchone()[0]
    assert top_title == "holiday-be"


def test_query_with_only_stopwords_returns_nothing(conn):
    assert conn.execute("SELECT * FROM kb.bm25_search('the and of')").fetchall() == []
