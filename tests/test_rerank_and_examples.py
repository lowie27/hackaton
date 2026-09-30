"""Cross-encoder rerank step (with a fake model) and the two upload examples."""

import re
from pathlib import Path

import pytest

from conftest import make_kb, make_world, requires_db
from kb import rbac
from kb.ingest import load_directory

pytestmark = requires_db

DATA = Path(__file__).resolve().parents[1] / "data"


class MayReranker:
    """Stands in for the cross-encoder: it only cares whether the passage mentions May."""

    def __init__(self):
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, list(texts)))
        return [2.0 if re.search(r"\bmay\b", t, re.I) else -2.0 for t in texts]


def test_rerank_reorders_and_explains(conn):
    w = make_world(conn)
    reranker = MayReranker()
    w.kb.retriever.reranker = reranker
    # BM25 prefers "a" (query words repeated), the reranker prefers "b" (answers "when").
    w.upload(w.anna, "a", "double holiday pay double holiday pay", ["t-be"])
    w.upload(w.anna, "b", "holiday pay is paid in May", ["t-be"])

    plain = w.kb.search(w.bram, "double holiday pay may", context_ranking=False, rerank=False)
    reranked = w.kb.search(w.bram, "double holiday pay may", context_ranking=False)

    assert [h.title for h in plain][0] == "a"
    assert [h.title for h in reranked] == ["b", "a"]
    top = reranked[0]
    assert top.rerank_rank == 1 and top.rerank_score == pytest.approx(0.8808, abs=1e-4)  # sigmoid(2)
    assert top.relevance == top.rerank_score
    assert all(h.rerank_rank is None for h in plain)
    # The reranker only sees the candidates the user may read, with the title included.
    [(query, texts)] = reranker.calls
    assert query == "double holiday pay may" and len(texts) == 2 and texts[0].startswith(("a\n\n", "b\n\n"))


def test_rerank_never_sees_hidden_documents(conn):
    w = make_world(conn)
    reranker = MayReranker()
    w.kb.retriever.reranker = reranker
    w.upload(w.noor, "nl", "holiday allowance secret", ["t-nl"])
    assert w.kb.search(w.bram, "holiday allowance") == []
    assert reranker.calls == []


def test_rerank_requested_without_model_is_an_error(conn):
    w = make_world(conn)
    with pytest.raises(ValueError):
        w.kb.search(w.bram, "holiday", rerank=True)


def test_upload_examples_unique_and_near_duplicate(conn):
    kb = make_kb(conn)
    kb.seed(DATA / "sample" / "seed.json")
    bram = rbac.user_id_by_email(conn, "bram@example.com")
    eva = rbac.user_id_by_email(conn, "eva@example.com")
    examples = {Path(d.external_id).stem: d for d in load_directory(DATA / "upload_examples")}
    assert set(examples) == {"unique_bike_lease", "similar_remote_work_copy"}

    assert kb.upload(eva, examples["unique_bike_lease"], ["hr-be"]).similar == []  # an official policy: manager only
    [match] = kb.upload(bram, examples["similar_remote_work_copy"], ["hr-be"]).similar
    assert match.title == "Home-working allowance (Belgium)"
