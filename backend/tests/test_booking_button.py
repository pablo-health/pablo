# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's "Book a session" link: the parts that need no database.

Which outside addresses can be linked to, and how a website's marked link is
given its address or taken out. Which address each surface shows, from real rows, is proven
in ``tests_integration/database/test_booking_hosts_db.py``.
"""

from __future__ import annotations

import pytest
from app.portal.booking_button import InvalidOutsideUrlError, checked_outside_url
from app.sites.book_button import book_link_tag, with_book_link


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


URL = "https://portal.example.com/book/intro"


def test_a_marked_link_is_given_the_address_and_keeps_the_rest() -> None:
    page = b'<nav><a class="btn" href="#schedule" data-pablo="book">Schedule a visit</a></nav>'

    assert with_book_link(page, URL) == (
        b'<nav><a href="https://portal.example.com/book/intro" class="btn" data-pablo="book">'
        b"Schedule a visit</a></nav>"
    )


def test_a_marked_link_with_no_address_gets_one() -> None:
    assert with_book_link(b"<A DATA-PABLO=book>Book</A>", URL) == (
        b'<a href="https://portal.example.com/book/intro" DATA-PABLO=book>Book</A>'
    )


def test_with_no_address_every_marked_link_is_taken_out_with_its_text() -> None:
    page = (
        b"<p>Hi <a data-pablo='book' title='a > b'>Book <b>now</b></a>.</p>"
        b'<footer><a href="/about">About</a><a data-pablo="book">Book</a ></footer>'
    )

    assert with_book_link(page, None) == (b'<p>Hi .</p><footer><a href="/about">About</a></footer>')


def test_other_links_and_unmarked_pages_are_left_alone() -> None:
    page = b'<a href="/x" data-pablo="cta">Go</a><a title=\'data-pablo="book"\'>No</a>'

    assert with_book_link(page, URL) == page
    assert with_book_link(page, None) == page
    assert with_book_link(b"<h1>Hi</h1>", None) == b"<h1>Hi</h1>"


def test_the_address_is_escaped_into_the_attribute() -> None:
    served = with_book_link(b'<a data-pablo="book">B</a>', "https://example.com/?a=1&b=\u00e9")
    assert served.startswith(b'<a href="https://example.com/?a=1&amp;b=&#233;"')


def test_a_different_address_names_a_different_page() -> None:
    assert book_link_tag(URL) == book_link_tag(URL)
    assert book_link_tag(URL) != book_link_tag("https://e.com/b")
