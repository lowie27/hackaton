"""Where results disagree (kb.conflicts)."""

from types import SimpleNamespace

from conftest import make_kb, requires_db
from kb import rbac
from kb.conflicts import annotate, extract_facts, find_disputes
from kb.context import SIGNALS

POLICY = "Double holiday pay is calculated as 92% of the gross monthly salary."
TEAMS = "Double holiday pay is calculated as 93% of the gross monthly salary."
FAQ = "Double holiday pay is 85% of the gross monthly salary and is always paid in June."
NL = "In the Netherlands the holiday allowance is at least 8% of the gross annual salary."


def hit(doc_id, text, country="BE", title=None, trust=1.0):
    return SimpleNamespace(doc_id=doc_id, title=title or f"doc {doc_id}", text=text, country=country, trust=trust,
                           disagreements=[])


def test_extracts_percentages_amounts_and_durations():
    facts = extract_facts("You get EUR 150 net if you work from home one day per week. It is 2,5% for 36 months.")
    assert [(f.kind, f.value, f.text) for f in facts] == [
        ("amount", 150, "EUR 150"), ("day", 1, "one day"), ("percent", 2.5, "2,5%"), ("month", 36, "36 months"),
    ]


def test_three_versions_of_the_same_fact_are_one_dispute_in_rank_order():
    [d] = find_disputes([hit(1, POLICY), hit(2, TEAMS), hit(3, FAQ)])
    assert d.label == "percentage"
    assert [(c.rank, c.text) for c in d.claims] == [(1, "92%"), (2, "93%"), (3, "85%")]
    assert "double holiday pay" in d.topic


def test_other_country_is_not_a_contradiction():
    assert find_disputes([hit(1, POLICY), hit(2, NL, country="NL")]) == []


def test_global_document_is_compared():
    assert len(find_disputes([hit(1, POLICY), hit(2, TEAMS, country=None)])) == 1


def test_agreeing_documents_and_unrelated_numbers_are_no_dispute():
    unrelated = "Payslips are kept for at most five years after the end of employment."
    assert find_disputes([hit(1, POLICY), hit(2, POLICY), hit(3, unrelated)]) == []


def test_numbers_inside_one_document_are_not_compared():
    one = hit(1, "The salary is topped up to 100% in year one and to 85% of the salary in year two.")
    assert find_disputes([one]) == []


def test_annotate_tells_each_hit_who_disagrees():
    hits = [hit(1, POLICY, title="Policy"), hit(2, TEAMS, title="Teams copy"), hit(3, FAQ, title="FAQ")]
    annotate(hits)
    [d] = hits[1].disagreements  # only compared with the most trusted claim
    assert (d["value"], d["other_rank"], d["other_title"], d["other_value"]) == ("93%", 1, "Policy", "92%")
    assert not d["most_trusted"]
    assert [x["other_value"] for x in hits[0].disagreements] == ["93%", "85%"]
    assert hits[0].disagreements[0]["most_trusted"]


def test_disputes_need_a_relevant_result_near_the_top():
    far = [hit(1, "Expense claims need a receipt."), hit(2, "Parking is free."), hit(3, "Lunch is at noon."),
           hit(4, POLICY), hit(5, TEAMS)]
    assert find_disputes(far) == []
    off_topic = [hit(1, POLICY), hit(2, TEAMS)]
    for h in off_topic:
        h.rerank_score = 0.05  # the cross-encoder says: not an answer to this question
    assert find_disputes(off_topic) == []
    off_topic[0].rerank_score = 0.7
    assert len(find_disputes(off_topic)) == 1


def test_without_rerank_the_dispute_must_be_about_the_question():
    hits = [hit(1, POLICY), hit(2, TEAMS)]
    assert find_disputes(hits, "salary during sick leave") == []
    assert len(find_disputes(hits, "double holiday pay")) == 1


def test_trust_not_rank_decides_which_claim_wins():
    # The informal copy ranks first on text relevance, but the policy is more trustworthy.
    [d] = find_disputes([hit(1, TEAMS, title="Teams", trust=0.8), hit(2, POLICY, title="Policy", trust=1.4)])
    assert [c.title for c in d.claims] == ["Policy", "Teams"]


def test_long_and_short_sentences_about_the_same_allowance():
    policy = ("Employees in Belgium who work from home at least one day per week receive a monthly "
              "home-working allowance of EUR 150 net, paid with the regular salary.")
    chat = ("Quick answer from the team chat: the home office allowance is EUR 129 per month, "
            "you only get it if you work from home two days a week.")
    for order in ([hit(1, chat), hit(2, policy)], [hit(1, policy), hit(2, chat)]):
        assert {d.label for d in find_disputes(order)} == {"amount", "number of days"}


@requires_db
def test_search_flags_the_holiday_pay_contradiction_in_the_demo_data(conn):
    from pathlib import Path

    kb = make_kb(conn)
    kb.seed(Path(__file__).resolve().parents[1] / "data" / "sample" / "seed.json")
    bram = rbac.user_id_by_email(conn, "bram@example.com")
    hits = kb.search(bram, "double holiday pay", top_k=10)
    values = {d["value"] for h in hits for d in h.disagreements if d["label"] == "percentage"}
    assert {"92%", "93%", "85%"} <= values
    # The official 2025 policy ranks first, so it is the reference the others disagree with.
    top = hits[0]
    assert top.title == "Holiday pay for white-collar employees (Belgium)"
    assert {d["other_value"] for d in top.disagreements} == {"93%", "85%"}
    assert not kb.search(bram, "double holiday pay", detect_conflicts=False)[0].disagreements


@requires_db
def test_contradicting_a_more_trustworthy_source_costs_rank(conn):
    from pathlib import Path

    kb = make_kb(conn)
    kb.seed(Path(__file__).resolve().parents[1] / "data" / "sample" / "seed.json")
    bram = rbac.user_id_by_email(conn, "bram@example.com")
    q = "how much is the home office allowance"
    policy, chat = "Home-working allowance (Belgium)", "Re - how much is the home office allowance now?"

    class LiveLikeReranker:
        """Scores like the real cross-encoder did live: the chat reads as the most direct answer."""

        def score(self, query, texts):
            return [1.53 if t.startswith(chat) else -0.21 if t.startswith(policy) else -3.0 for t in texts]

    kb.retriever.reranker = LiveLikeReranker()

    without = [h.title for h in kb.search(bram, q, signals=[s for s in SIGNALS if s != "conflicts"])]
    hits = kb.search(bram, q)
    titles = [h.title for h in hits]
    assert titles.index(policy) < titles.index(chat)
    assert without.index(chat) < without.index(policy)  # the chat only loses once the conflict counts
    demoted = hits[titles.index(chat)]
    assert "contradicts a more trustworthy source on the amount (EUR 129 vs EUR 150)" in demoted.warnings
    # Ranks in the disagreements match the final order.
    assert {d["other_rank"] for d in demoted.disagreements} == {titles.index(policy) + 1}
