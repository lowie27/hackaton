from kb.ingest import chunk_text, parse_frontmatter


def test_frontmatter_is_split_from_body():
    meta, body = parse_frontmatter("---\ntitle: Leave\ncountry: BE\nbogus: x\n---\nHello")
    assert meta == {"title": "Leave", "country": "BE"}
    assert body == "Hello"


def test_no_frontmatter_returns_text_unchanged():
    assert parse_frontmatter("just text") == ({}, "just text")


def test_paragraphs_are_packed_up_to_limit():
    text = "\n\n".join(["one two three"] * 4)
    assert chunk_text(text, max_words=6) == ["one two three\n\none two three"] * 2


def test_oversized_paragraph_is_hard_split():
    text = "a b c d e f g"
    assert chunk_text(text, max_words=3) == ["a b c", "d e f", "g"]


def test_empty_text_has_no_chunks():
    assert chunk_text("  \n\n  ") == []
