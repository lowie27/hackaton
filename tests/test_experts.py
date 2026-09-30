"""Recommending a person: when (kb.escalation) and who (kb.experts)."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import requires_db
from kb import experts
from kb.config import Settings
from kb.conflicts import Claim, Dispute
from kb.context import UserContext
from kb.db import connect
from kb.escalation import assess_results

TODAY = date(2026, 9, 30)
BRAM = UserContext(country="BE", location="Antwerp", department="Payroll")
EXPERTS = Path(__file__).resolve().parents[1] / "data" / "sample" / "experts.json"


def hit(title, text="some text", rerank=None, trust=1.0, warnings=(), owner=None, country="BE", doc_id=None):
    return SimpleNamespace(title=title, text=text, rerank_score=rerank, trust=trust, warnings=list(warnings),
                           owner=owner, country=country, doc_id=doc_id or title)


def dispute(*claims):
    return Dispute("percent", "double holiday pay",
                   [Claim(rank, rank, title, value, f"{value:g}%", "…", trust) for rank, title, value, trust in claims])


# --- when ---------------------------------------------------------------------

def test_good_answer_needs_nobody():
    assert assess_results([hit("Policy", rerank=0.8, trust=1.5)]).level is None


def test_no_results_or_no_real_answer_means_ask():
    assert assess_results([]).level == "ask"
    e = assess_results([hit("Calendar", rerank=0.05)])
    assert e.level == "ask" and "5% relevant" in e.reasons[0]


def test_untrustworthy_best_answer_means_ask():
    e = assess_results([hit("FAQ", rerank=0.9, trust=0.5, warnings=["expired on 2023-12-31"])])
    assert e.level == "ask" and "expired on 2023-12-31" in e.reasons[0]


def test_clear_winner_in_a_dispute_means_confirm_with_the_owner():
    e = assess_results([hit("Policy", rerank=0.8, trust=1.8)], [dispute((1, "Policy", 92, 1.8), (2, "Teams", 93, 0.9))])
    assert e.level == "confirm" and "92%, 93%" in e.reasons[0]


def test_dispute_without_clear_winner_means_ask():
    e = assess_results([hit("A", rerank=0.8, trust=1.2)], [dispute((1, "A", 92, 1.2), (2, "B", 93, 1.1))])
    assert e.level == "ask" and "none is clearly more trustworthy" in e.reasons[0]


def test_near_identical_copies_mean_confirm():
    text = "White-collar employees receive double holiday pay in May or June, calculated on gross salary."
    e = assess_results([hit("A", text, rerank=0.9, trust=1.5), hit("B", text + " Copy.", rerank=0.9)])
    assert e.level == "confirm" and "near-identical" in e.reasons[0]


# --- who ----------------------------------------------------------------------

def test_expert_context_signals():
    person = {"country": "BE", "department": "HR", "location": "Antwerp", "owns": ["HR BE - Mobility"],
              "away_until": date(2026, 10, 14), "last_active": date(2026, 9, 12), "answered": 22}
    trust, reasons, warnings = experts.assess_expert(
        person, BRAM, TODAY, {"HR BE - Mobility": "Company car policy (Belgium)"}, None, {"BE"})
    assert "owns 'Company car policy (Belgium)' in your results" in reasons
    assert "out of office until 2026-10-14" in warnings
    assert trust == pytest.approx(1 + experts.EXPERT_COUNTRY_MATCH + experts.EXPERT_LOCATION_MATCH
                                  + experts.OWNS_RESULT + experts.AWAY)


@pytest.fixture
def xconn():
    with connect() as main:
        experts.ensure_database(main)
    with experts.connect() as c:
        with c.transaction(force_rollback=True):
            experts.init_schema(c)
            experts.seed(c, EXPERTS)
            yield c


@requires_db
def test_owner_of_the_trusted_answer_ranks_first(xconn):
    hits = [hit("Holiday pay for white-collar employees (Belgium)", owner="Payroll BE - Legal Desk"),
            hit("Holiday pay FAQ (old intranet page)")]
    d = dispute((1, "Holiday pay for white-collar employees (Belgium)", 92, 1.8), (2, "Holiday pay FAQ (old intranet page)", 85, 0.1))
    [top, *_] = experts.find_experts(xconn, Settings(), "double holiday pay", BRAM, hits, [d], today=TODAY)
    assert top.name == "Lotte Peeters"
    assert "owns the most trustworthy version, so can confirm it" in top.reasons


@requires_db
def test_available_colleague_beats_the_owner_who_is_away(xconn):
    hits = [hit("Company car policy (Belgium)", owner="HR BE - Mobility")]
    people = experts.find_experts(xconn, Settings(), "can I still order a hybrid company car", BRAM, hits, today=TODAY)
    names = [p.name for p in people]
    assert names.index("Karel De Vos") < names.index("Tom Claes")
    tom = people[names.index("Tom Claes")]
    assert "out of office until 2026-10-14" in tom.warnings


@requires_db
def test_expert_for_a_question_without_documents(xconn):
    [top, *_] = experts.find_experts(xconn, Settings(), "how long do we keep payslips gdpr", BRAM, [], today=TODAY)
    assert top.name == "Marie Lambert" and "group-wide role" in top.reasons


def test_off_topic_results_are_not_near_copies():
    text = "White-collar employees receive double holiday pay in May or June, calculated on gross salary."
    e = assess_results([hit("Car", rerank=0.8, trust=1.5), hit("A", text, rerank=0.05), hit("B", text + " Copy.", rerank=0.05)])
    assert e.level is None


@requires_db
def test_off_topic_results_do_not_set_the_country(xconn):
    hits = [hit("Dubbel vakantiegeld", rerank=0.15, country="FR", owner="Payroll FR")]
    people = experts.find_experts(xconn, Settings(), "salary during sick leave", BRAM, hits, today=TODAY)
    femke = next(p for p in people if p.name == "Femke de Vries")
    assert "works in NL, the question is about BE" in femke.warnings
