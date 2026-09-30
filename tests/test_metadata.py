"""Metadata on upload, search filters and context ranking against Postgres."""

from datetime import date

import pytest

from conftest import make_world, requires_db
from kb import rbac
from kb.ingest import Document
from kb.retriever import SearchFilters

pytestmark = requires_db

TODAY = date(2026, 9, 30)
TEXT = "Double holiday pay for white-collar employees is calculated on the gross monthly salary."


def profile(conn, w):
    # Same users as make_world, now with a profile.
    rbac.upsert_user(conn, "t-anna@example.com", "anna", country="be", location="Antwerp",
                     department="Payroll", position="Payroll BE lead")
    rbac.upsert_user(conn, "t-bram@example.com", "bram", country="BE", location="Antwerp",
                     department="Payroll", position="Payroll consultant")
    rbac.upsert_user(conn, "t-noor@example.com", "noor", country="NL", department="Payroll")


def upload(w, user, ext_id, groups=("t-be",), body=TEXT, **meta):
    return w.kb.upload(user, Document(ext_id, ext_id, body, meta), groups)


def search(w, user, query="double holiday pay", top_k=10, **filters):
    return w.kb.retriever.search(user, query, top_k, filters=SearchFilters(**filters), today=TODAY)


def row(conn, doc_id, *cols):
    return conn.execute(f"SELECT {', '.join(cols)} FROM kb.documents WHERE id = %s", (doc_id,)).fetchone()


def test_metadata_is_stored_and_normalised(conn):
    w = make_world(conn)
    profile(conn, w)
    doc_id = upload(w, w.anna, "m", country=" be ", language="EN", tags="Holiday Pay, exit, holiday pay",
                    department="Payroll", location="Antwerp", valid_from="2025-01-01", valid_until="2025-12-31").doc_id
    assert row(conn, doc_id, "country", "language", "tags", "department", "location", "valid_from", "valid_until") == (
        "BE", "en", ["holiday pay", "exit"], "Payroll", "Antwerp", date(2025, 1, 1), date(2025, 12, 31))


def test_uploader_snapshot_comes_from_the_profile_not_the_document(conn):
    w = make_world(conn)
    profile(conn, w)
    # A member tries to claim authority through frontmatter-like metadata: ignored.
    doc_id = upload(w, w.bram, "b", uploader_position="CEO", uploader_is_manager="true").doc_id
    assert row(conn, doc_id, "uploader_position", "uploader_department", "uploader_is_manager") == (
        "Payroll consultant", "Payroll", False)
    doc_id = upload(w, w.anna, "a").doc_id
    assert row(conn, doc_id, "uploader_position", "uploader_is_manager") == ("Payroll BE lead", True)


def test_manager_flag_only_counts_for_the_shared_groups(conn):
    w = make_world(conn)
    profile(conn, w)
    doc_id = upload(w, w.anna, "private", groups=()).doc_id
    assert row(conn, doc_id, "uploader_is_manager") == (False,)


def test_valid_until_before_valid_from_is_rejected(conn):
    w = make_world(conn)
    with pytest.raises(ValueError):
        upload(w, w.anna, "bad", valid_from="2025-12-31", valid_until="2025-01-01")


def test_reupload_updates_metadata(conn):
    w = make_world(conn)
    doc_id = upload(w, w.anna, "r", tags="old").doc_id
    upload(w, w.anna, "r", tags="new", valid_until="2025-01-01")
    assert row(conn, doc_id, "tags", "valid_until") == (["new"], date(2025, 1, 1))


def test_frontmatter_carries_the_new_keys():
    from kb.ingest import parse_frontmatter

    meta, _ = parse_frontmatter("---\ndepartment: Payroll\nlocation: Ghent\nlanguage: nl\ntags: a, b\n"
                                "valid_from: 2025-01-01\nvalid_until: 2025-12-31\nuploader_position: CEO\n---\nx")
    assert meta == {"department": "Payroll", "location": "Ghent", "language": "nl", "tags": "a, b",
                    "valid_from": "2025-01-01", "valid_until": "2025-12-31"}


def titles(hits):
    return [h.title for h in hits]


def test_filters(conn):
    w = make_world(conn)
    upload(w, w.anna, "be", country="BE", department="Payroll", source="policy", language="en", tags="holiday pay")
    upload(w, w.anna, "global", department="HR", source="wiki", language="nl", tags="holiday pay, faq")
    upload(w, w.anna, "fr", country="FR", source="policy", language="en", valid_until="2024-12-31")

    assert set(titles(search(w, w.bram, country="be"))) == {"be", "global"}
    assert set(titles(search(w, w.bram, department="payroll"))) == {"be", "fr"}  # fr has no department
    assert set(titles(search(w, w.bram, source="Policy"))) == {"be", "fr"}
    assert titles(search(w, w.bram, language="NL")) == ["global"]
    assert titles(search(w, w.bram, tags=["FAQ", "holiday pay"])) == ["global"]
    assert set(titles(search(w, w.bram, valid_on=TODAY))) == {"be", "global"}
    assert set(titles(search(w, w.bram, valid_on=date(2024, 6, 1)))) == {"be", "global", "fr"}


def test_filters_do_not_bypass_rbac(conn):
    w = make_world(conn)
    upload(w, w.noor, "nl", groups=("t-nl",), country="NL")
    assert search(w, w.bram, country="NL") == []


def test_filtered_out_chunks_do_not_use_up_top_k(conn):
    w = make_world(conn)
    for i in range(30):
        upload(w, w.anna, f"fr-{i}", body="holiday holiday holiday pay", country="FR")
    upload(w, w.anna, "be", country="BE")
    assert titles(search(w, w.bram, "holiday pay", top_k=1, country="BE")) == ["be"]


def test_context_ranking_prefers_what_applies_to_the_user(conn):
    w = make_world(conn)
    profile(conn, w)
    # Same text: relevance ties, so only the metadata decides.
    upload(w, w.anna, "nl-version", country="NL", source="policy", owner="Payroll NL", updated_at="2026-06-01")
    upload(w, w.anna, "old-be", country="BE", updated_at="2019-06-01", valid_until="2024-12-31")
    upload(w, w.anna, "be-policy", country="BE", department="Payroll", source="policy", owner="Legal Desk",
           updated_at="2025-02-10", valid_from="2025-01-01")

    hits = search(w, w.bram)
    assert titles(hits) == ["be-policy", "nl-version", "old-be"]
    top, nl, old = hits
    assert "applies to your country (BE)" in top.reasons and top.warnings == []
    assert "applies to NL, you work in BE" in nl.warnings
    assert "expired on 2024-12-31" in old.warnings
    assert top.score > top.relevance and old.score < old.relevance
    # The explanation fields reach the UI.
    assert (top.department, top.uploader_position, top.uploader_is_manager) == ("Payroll", "Payroll BE lead", True)

    # Noor sees the NL version first for the same query (after it is shared with her).
    rbac.add_member(conn, rbac.upsert_group(conn, "t-be"), w.noor, "member")
    assert titles(search(w, w.noor))[0] == "nl-version"


def test_context_ranking_can_be_switched_off(conn):
    w = make_world(conn, context_ranking=False)
    profile(conn, w)
    upload(w, w.anna, "x", country="NL")
    [hit] = search(w, w.bram)
    assert hit.reasons == [] and hit.warnings == [] and hit.score == hit.relevance
