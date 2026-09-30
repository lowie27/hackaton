import pytest

from conftest import make_world, requires_db
from kb.db import user_session
from kb.rbac import PermissionDenied

pytestmark = requires_db

BE_TEXT = "Belgian double holiday pay is 92 percent of gross monthly salary."
NL_TEXT = "Dutch holiday allowance is eight percent of gross annual salary."


def titles(hits):
    return {h.title for h in hits}


def test_search_only_returns_documents_shared_with_the_user(conn):
    w = make_world(conn)
    w.upload(w.anna, "be", BE_TEXT, ["t-be"])
    w.upload(w.noor, "nl", NL_TEXT, ["t-nl"])
    w.upload(w.eve, "private", "holiday salary notes")

    assert titles(w.kb.search(w.bram, "holiday salary")) == {"be"}
    assert titles(w.kb.search(w.noor, "holiday salary")) == {"nl"}
    assert titles(w.kb.search(w.eve, "holiday salary")) == {"private"}  # own upload, no groups
    assert {"be", "nl", "private"} <= titles(w.kb.search(w.admin, "holiday salary", top_k=50))


def test_hidden_documents_do_not_use_up_top_k(conn):
    w = make_world(conn)
    for i in range(10):
        w.upload(w.noor, f"nl-{i}", "holiday holiday holiday allowance", ["t-nl"])
    w.upload(w.anna, "be", BE_TEXT, ["t-be"])

    hits = w.kb.search(w.bram, "holiday", top_k=1)
    assert titles(hits) == {"be"}


def test_unknown_user_sees_nothing(conn):
    w = make_world(conn)
    w.upload(w.anna, "be", BE_TEXT, ["t-be"])
    assert w.kb.search(987654321, "holiday") == []
    with user_session(conn, 987654321):
        assert conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] == 0


def test_session_role_is_reset_after_user_session(conn):
    make_world(conn)
    before = conn.execute("SELECT current_user").fetchone()[0]
    with user_session(conn, 1):
        assert conn.execute("SELECT current_user").fetchone()[0] == "kb_app"
    assert conn.execute("SELECT current_user").fetchone()[0] == before


def test_cannot_share_with_group_you_are_not_in(conn):
    w = make_world(conn)
    with pytest.raises(PermissionDenied):
        w.upload(w.bram, "sneaky", BE_TEXT, ["t-nl"])


def test_cannot_overwrite_someone_elses_document(conn):
    w = make_world(conn)
    w.upload(w.bram, "be", BE_TEXT, ["t-be"])
    with pytest.raises(PermissionDenied):
        w.upload(w.noor, "be", "replaced", ["t-nl"])
    # A manager of the document's group may update it.
    w.upload(w.anna, "be", BE_TEXT + " Updated.", ["t-be"])


def test_notifications_are_private(conn):
    w = make_world(conn)
    w.upload(w.bram, "old", BE_TEXT, ["t-be"])
    w.upload(w.anna, "new", BE_TEXT, ["t-be"])
    [note] = w.kb.notifications(w.bram)

    assert not w.kb.mark_notification_read(w.noor, note.id)
    assert w.kb.notifications(w.noor) == []
    assert w.kb.mark_notification_read(w.bram, note.id)
    assert w.kb.notifications(w.bram, unread_only=True) == []
