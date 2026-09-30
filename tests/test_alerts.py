from conftest import make_world, requires_db

pytestmark = requires_db

POLICY = (
    "Double holiday pay for white-collar employees in Belgium is calculated as 92 percent "
    "of the gross monthly salary and is paid in May or June."
)
UNRELATED = "Expense claims must be submitted within thirty days together with a receipt."


def messages(kb, user):
    return [n.message for n in kb.notifications(user)]


def test_near_duplicate_notifies_uploader_existing_uploader_and_manager(conn):
    w = make_world(conn)
    old = w.upload(w.bram, "old", POLICY, ["t-be"], title="Old FAQ")
    new = w.upload(w.anna, "new", POLICY + " Updated 2025.", ["t-be"], title="New policy")

    [match] = new.similar
    assert (match.doc_id, match.title) == (old.doc_id, "Old FAQ")
    assert match.lexical_score >= w.kb.settings.duplicate_lexical_threshold

    [to_anna] = messages(w.kb, w.anna)  # uploader and manager: one notification
    assert to_anna.startswith("Your upload 'New policy' looks similar to 'Old FAQ'")
    [to_bram] = messages(w.kb, w.bram)
    assert "'New policy'" in to_bram and "'Old FAQ'" in to_bram
    assert messages(w.kb, w.noor) == []


def test_titles_of_unreadable_documents_are_redacted(conn):
    w = make_world(conn)
    w.upload(w.bram, "be", POLICY, ["t-be"], title="BE policy")
    result = w.upload(w.noor, "nl-copy", POLICY, ["t-nl"], title="NL copy")

    [match] = result.similar
    assert match.doc_id is None and match.title is None
    [to_noor] = messages(w.kb, w.noor)
    assert "BE policy" not in to_noor and "a document you don't have access to" in to_noor
    [to_anna] = w.kb.notifications(w.anna)
    assert "NL copy" not in to_anna.message
    assert to_anna.new_doc_id is None and to_anna.existing_doc_id is not None


def test_reupload_does_not_notify_twice(conn):
    w = make_world(conn)
    w.upload(w.bram, "old", POLICY, ["t-be"])
    w.upload(w.anna, "new", POLICY, ["t-be"])
    w.upload(w.anna, "new", POLICY + " Typo fixed.", ["t-be"])
    assert len(messages(w.kb, w.bram)) == 1


def test_unrelated_upload_raises_no_alert(conn):
    w = make_world(conn)
    w.upload(w.bram, "policy", POLICY, ["t-be"])
    assert w.upload(w.anna, "expenses", UNRELATED, ["t-be"]).similar == []
    assert messages(w.kb, w.bram) == []


def test_admins_are_notified_when_no_group_has_a_manager(conn):
    w = make_world(conn)
    w.upload(w.eve, "a", POLICY)
    w.upload(w.eve, "b", POLICY)
    assert len(messages(w.kb, w.admin)) == 1
