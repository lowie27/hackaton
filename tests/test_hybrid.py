import pytest

from conftest import make_kb, make_world, requires_db
from kb.retriever import reciprocal_rank_fusion

BE_TEXT = "Belgian double holiday pay is 92 percent of gross monthly salary."
NL_TEXT = "Dutch holiday allowance is eight percent of gross annual salary."


def test_rrf_rewards_items_ranked_by_both_lists():
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]], k=60)
    assert max(scores, key=scores.get) == 1
    assert scores[3] > scores[2]
    assert scores[4] == pytest.approx(1 / 63)


@pytest.fixture
def vector_conn(conn):
    if not conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'").fetchone():
        pytest.skip("pgvector not installed (use the pgvector/pgvector image)")
    return conn


@requires_db
def test_hybrid_combines_both_rankings(vector_conn):
    w = make_world(vector_conn, vector_enabled=True, min_vector_similarity=0.0)
    w.upload(w.anna, "be", BE_TEXT, ["t-be"])
    w.upload(w.anna, "nl", NL_TEXT, ["t-be"])

    hybrid = w.kb.search(w.bram, "double holiday pay")
    assert hybrid[0].title == "be"
    assert hybrid[0].bm25_rank == 1 and hybrid[0].vector_rank == 1

    bm25 = w.kb.search(w.bram, "double holiday pay", mode="bm25")
    assert all(h.vector_rank is None for h in bm25)
    vector = w.kb.search(w.bram, "double holiday pay", mode="vector")
    assert all(h.bm25_rank is None for h in vector)


@requires_db
def test_vector_search_respects_rbac(vector_conn):
    w = make_world(vector_conn, vector_enabled=True, min_vector_similarity=0.0)
    w.upload(w.noor, "nl", NL_TEXT, ["t-nl"])
    assert w.kb.search(w.bram, "holiday allowance", mode="vector") == []


@requires_db
def test_vector_modes_rejected_when_disabled(conn):
    w = make_world(conn)
    with pytest.raises(ValueError):
        w.kb.search(w.bram, "holiday", mode="hybrid")


@requires_db
def test_vector_duplicate_detection(vector_conn):
    # Lexical detection switched off: only the embedding similarity can raise the alert.
    w = make_world(vector_conn, vector_enabled=True, duplicate_lexical_threshold=1.01)
    w.upload(w.anna, "a", BE_TEXT, ["t-be"], title="Holiday pay")
    [match] = w.upload(w.anna, "b", BE_TEXT + " Confirmed.", ["t-be"], title="Holiday pay").similar
    assert match.lexical_score is None and match.vector_score > 0.9


@requires_db
def test_embed_missing_backfills_after_enabling(vector_conn):
    w = make_world(vector_conn)  # vectors off: chunks stored without embeddings
    w.upload(w.anna, "be", BE_TEXT, ["t-be"])
    on = make_kb(vector_conn, vector_enabled=True, min_vector_similarity=0.0)
    assert on.embed_missing() >= 1
    assert on.search(w.bram, "holiday", mode="vector", top_k=1)[0].title == "be"
