# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's "Book a session" link: the parts that need no database.

Which outside addresses can be linked to, and how the link is added to a
website's pages. Which address each surface shows, from real rows, is proven
in ``tests_integration/database/test_booking_button_db.py``.
"""

from __future__ import annotations

import pytest
from app.portal.booking_button import InvalidOutsideUrlError, checked_outside_url
from app.sites.book_button import BookButton, book_button_for, with_book_button
from app.sites.theme import PracticeTheme, ThemeColors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://book.example.com/riverside", "https://book.example.com/riverside"),
        (
            "  https://example.com/schedule?with=sam#top  ",
            "https://example.com/schedule?with=sam#top",
        ),
    ],
)
def test_an_https_address_is_kept(raw: str, expected: str) -> None:
    assert checked_outside_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://example.com/book",
        "javascript:alert(1)",
        "https://",
        "https://localhost/book",
        "https://user:secret@example.com/book",
        'https://example.com/"onmouseover="x',
        "https://example.com/a b",
        "https://example.com/<script>",
        "https://example.com/" + "a" * 2048,
    ],
)
def test_an_address_that_cannot_be_linked_to_is_refused(raw: str) -> None:
    with pytest.raises(InvalidOutsideUrlError):
        checked_outside_url(raw)


def test_the_link_goes_just_before_the_last_closing_body() -> None:
    page = b"<html><body><p>Hi</p><!-- </body> --></BODY ></html>"

    served = with_book_button(page, BookButton(url="https://portal.example.com/book/intro"))

    before, _, after = served.partition(b"<a ")
    assert before == b"<html><body><p>Hi</p><!-- </body> -->"
    assert after.endswith(b">Book a session</a></BODY ></html>")
    assert b'href="https://portal.example.com/book/intro"' in served
    assert b"data-pablo-book-session" in served


def test_a_page_with_no_closing_body_gets_the_link_at_its_end() -> None:
    served = with_book_button(b"<h1>Hi</h1>", BookButton(url="https://example.com/b"))
    assert served.startswith(b"<h1>Hi</h1><a ")
    assert served.endswith(b"Book a session</a>")


def test_the_address_is_escaped_into_the_attribute() -> None:
    served = with_book_button(b"", BookButton(url="https://example.com/?a=1&b=2"))
    assert b'href="https://example.com/?a=1&amp;b=2"' in served


def test_the_link_wears_the_themes_accent_pair_or_its_own_colors() -> None:
    themed = PracticeTheme(colors=ThemeColors(accent="#24504c", accent_text="#ffffff"))
    accent_only = PracticeTheme(colors=ThemeColors(accent="#24504c"))

    assert book_button_for(None, themed) is None
    assert book_button_for("https://e.com/b", themed) == BookButton(
        url="https://e.com/b", background="#24504c", color="#ffffff"
    )
    assert book_button_for("https://e.com/b", accent_only) == BookButton(url="https://e.com/b")
    assert book_button_for("https://e.com/b", None) == BookButton(url="https://e.com/b")


def test_a_different_link_names_a_different_page() -> None:
    one = BookButton(url="https://e.com/a")
    assert one.tag == BookButton(url="https://e.com/a").tag
    assert one.tag != BookButton(url="https://e.com/b").tag
    assert one.tag != BookButton(url="https://e.com/a", background="#000000").tag
