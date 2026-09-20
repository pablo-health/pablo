# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rendering a consent document, and digesting it.

Two properties are being pinned, and they are the reason this module exists
rather than a dependency.

**Nothing a practice types can become markup.** The renderer escapes the
source before it emits a single tag, so a ``<script>`` in a consent document
is words on a page. The tests below paste in the things that would matter if
that were not true — a script tag, an iframe, an ``onerror`` attribute, a
``javascript:`` link — and assert the output has no live markup in it, while
the ordinary things a consent document is made of still render.

**The digest is stable, and it moves when the words move.** A signature
records the digest of the text somebody agreed to, so the two directions are
both load-bearing: re-rendering the same document must not produce a new
digest, and changing one character must.
"""

from __future__ import annotations

from app.intake.documents import canonical_text, content_digest, render_html

_CONSENT = """# Consent for treatment

This practice provides **psychotherapy**. Please read this before your
first appointment.

## What you are agreeing to

- Sessions are 50 minutes.
- Give 24 hours notice to cancel.
- You can [read our policies](https://example.org/policies) at any time.

> Review this document with your attorney before you use it.

---

1. First.
2. Second.
"""


class TestWhatTheReaderGets:
    def test_headings_and_paragraphs_render(self) -> None:
        html = render_html(_CONSENT)
        assert "<h2>Consent for treatment</h2>" in html
        assert "<h3>What you are agreeing to</h3>" in html
        assert "<p>This practice provides <strong>psychotherapy</strong>." in html

    def test_a_top_level_heading_renders_below_the_page_heading(self) -> None:
        """``#`` is an ``h2``: the page it sits on already has an ``h1``."""
        assert render_html("# Title") == "<h2>Title</h2>"

    def test_both_kinds_of_list_render(self) -> None:
        html = render_html(_CONSENT)
        assert "<ul><li>Sessions are 50 minutes.</li>" in html
        assert "<ol><li>First.</li><li>Second.</li></ol>" in html

    def test_a_block_quote_and_a_rule_render(self) -> None:
        html = render_html(_CONSENT)
        assert "<blockquote><p>Review this document with your attorney" in html
        assert "<hr />" in html

    def test_a_paragraph_is_rewrapped_into_one_line(self) -> None:
        """Two source lines are one paragraph, as markdown says they are."""
        assert render_html("one\ntwo") == "<p>one two</p>"

    def test_a_link_renders_with_its_address(self) -> None:
        html = render_html("[policies](https://example.org/p)")
        assert '<a href="https://example.org/p"' in html
        assert ">policies</a>" in html

    def test_an_external_link_cannot_reach_back(self) -> None:
        html = render_html("[policies](https://example.org/p)")
        assert 'rel="noopener noreferrer nofollow"' in html
        assert 'target="_blank"' in html


class TestWhatCannotGetThrough:
    def test_a_script_tag_is_words(self) -> None:
        html = render_html("Before\n\n<script>alert(1)</script>\n\nAfter")
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_an_iframe_is_words(self) -> None:
        html = render_html('<iframe src="https://evil.example"></iframe>')
        assert "<iframe" not in html
        assert "&lt;iframe" in html

    def test_an_event_handler_attribute_is_words(self) -> None:
        html = render_html('<img src=x onerror="alert(1)">')
        assert "<img" not in html
        assert 'onerror="' not in html

    def test_a_javascript_link_is_not_a_link(self) -> None:
        html = render_html("[click me](javascript:alert(1))")
        assert "<a " not in html
        assert "click me" in html

    def test_a_data_url_is_not_a_link(self) -> None:
        html = render_html("[pic](data:text/html;base64,PHNjcmlwdD4=)")
        assert "<a " not in html

    def test_an_entity_encoded_scheme_is_not_a_link(self) -> None:
        """``&#x6a;avascript:`` is the same scheme spelled to dodge a filter."""
        html = render_html("[go](&#x6a;avascript:alert(1))")
        assert "<a " not in html

    def test_a_relative_link_is_not_a_link(self) -> None:
        html = render_html("[somewhere](/elsewhere)")
        assert "<a " not in html
        assert "somewhere" in html

    def test_a_quote_in_a_link_cannot_break_out_of_the_attribute(self) -> None:
        html = render_html('[x](https://example.org/a"onmouseover="alert(1))')
        assert 'onmouseover="alert(1)"' not in html
        assert "&quot;" in html

    def test_the_only_tags_emitted_are_the_ones_this_module_writes(self) -> None:
        """A sweep, so a new renderer branch cannot quietly widen the set."""
        html = render_html(_CONSENT)
        opened = {part.split(">")[0].split(" ")[0] for part in html.split("<")[1:]}
        assert opened <= {
            "h2",
            "h3",
            "/h2",
            "/h3",
            "p",
            "/p",
            "ul",
            "/ul",
            "ol",
            "/ol",
            "li",
            "/li",
            "blockquote",
            "/blockquote",
            "strong",
            "/strong",
            "em",
            "/em",
            "a",
            "/a",
            "code",
            "/code",
            "hr",
        }


class TestTheDigest:
    def test_the_same_text_digests_the_same_twice(self) -> None:
        assert content_digest(_CONSENT) == content_digest(_CONSENT)

    def test_one_character_changes_it(self) -> None:
        changed = _CONSENT.replace("50 minutes", "60 minutes")
        assert content_digest(changed) != content_digest(_CONSENT)

    def test_reflowing_a_paragraph_does_not_change_it(self) -> None:
        """Whitespace is formatting. The words are what was agreed to."""
        assert content_digest("one two\nthree") == content_digest("one   two    three")

    def test_windows_line_endings_do_not_change_it(self) -> None:
        assert content_digest("one\r\n\r\ntwo") == content_digest("one\n\ntwo")

    def test_it_is_a_sha256_in_hex(self) -> None:
        digest = content_digest(_CONSENT)
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)

    def test_a_link_address_is_part_of_what_was_agreed(self) -> None:
        """Repointing a link without moving the digest would be a hole."""
        first = content_digest("[policies](https://example.org/a)")
        second = content_digest("[policies](https://example.org/b)")
        assert first != second


class TestTheCanonicalText:
    def test_it_is_the_words_one_block_to_a_line(self) -> None:
        assert canonical_text("# Title\n\nBody text.") == "Title\nBody text."

    def test_a_list_is_one_line_per_item(self) -> None:
        assert canonical_text("- one\n- two") == "one\ntwo"

    def test_markdown_punctuation_is_not_part_of_the_words(self) -> None:
        assert canonical_text("**bold** and *soft*") == "bold and soft"

    def test_a_rule_contributes_nothing(self) -> None:
        assert canonical_text("one\n\n---\n\ntwo") == "one\ntwo"

    def test_an_empty_document_is_an_empty_string(self) -> None:
        assert canonical_text("   \n\n  ") == ""
