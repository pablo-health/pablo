# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A portal header suggested from a website's ``index.html``.

``fixtures/sites/index.html`` is captured from a practice website as it was
uploaded, with the names and addresses in it replaced and its structure kept.
The suggestion it gives is the header that page shows. Saving the suggestion
with a draft and accepting it are proven against Postgres in
``tests_integration/database/test_practice_sites_db.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.sites import suggest
from app.sites.header import HeaderLink, PracticeHeader
from app.sites.suggest import MAX_INDEX_BYTES, suggest_header

CAPTURED = (Path(__file__).parent / "fixtures" / "sites" / "index.html").read_bytes()
SITE = "sample-practice.example"
PORTAL = "portal.sample-practice.example"
HOSTS = frozenset({SITE, PORTAL})
BRANDS = ("Pablo",)


def _suggest(html: str | bytes, hosts: frozenset[str] = HOSTS) -> suggest.HeaderSuggestion | None:
    data = html.encode() if isinstance(html, str) else html
    return suggest_header(data, hosts, frozenset({PORTAL}), BRANDS)


def test_the_captured_website_suggests_the_header_it_shows() -> None:
    suggestion = _suggest(CAPTURED)

    assert suggestion is not None
    assert suggestion.skipped == []
    assert suggestion.header == PracticeHeader(
        wordmark="Sample Counseling & Wellness",
        subtitle="Sample Clinician, PMHNP-BC",
        links=[
            HeaderLink(label="Services", href="/#services"),
            HeaderLink(label="About", href="/#about"),
            HeaderLink(label="Insurance", href="/#insurance"),
            HeaderLink(label="FAQ", href="/#faq"),
        ],
        cta=HeaderLink(label="Schedule a visit", href="/#schedule"),
    )


def test_markers_give_exactly_their_values() -> None:
    html = """
    <header>
      <a href="/" class="logo">Ignored by the markers</a>
      <div data-pablo-header>
        <span data-pablo="brand">Riverside Counseling</span>
        <span data-pablo="subtitle">Individual and couples therapy</span>
        <ul data-pablo="nav">
          <li><a href="/services">Services</a></li>
          <li><a href="about.html">About</a></li>
        </ul>
        <a data-pablo="nav" href="/fees">Fees</a>
        <a href="/blog">Not marked</a>
        <a data-pablo="cta" href="/book" class="plain">Book a time</a>
      </div>
    </header>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert suggestion.skipped == []
    assert suggestion.header == PracticeHeader(
        wordmark="Riverside Counseling",
        subtitle="Individual and couples therapy",
        links=[
            HeaderLink(label="Services", href="/services"),
            HeaderLink(label="About", href="/about.html"),
            HeaderLink(label="Fees", href="/fees"),
        ],
        cta=HeaderLink(label="Book a time", href="/book"),
    )


@pytest.mark.parametrize(
    "html",
    [
        "<!doctype html><h1>Riverside Counseling</h1><p>Welcome.</p>",
        "<!doctype html><header><h1>Riverside Counseling</h1></header>",
        "",
        "\xff\xfe not even text",
    ],
)
def test_a_page_with_no_header_or_navigation_suggests_nothing(html: str) -> None:
    assert _suggest(html) is None


def test_a_page_past_the_limit_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(_data: bytes) -> object:
        raise AssertionError

    monkeypatch.setattr(suggest, "_page", never)
    page = b"<header><a href='/'>Riverside</a></header>"

    assert _suggest(page + b" " * (MAX_INDEX_BYTES + 1 - len(page))) is None


def test_a_page_at_the_limit_is_read() -> None:
    page = b"<header><a href='/'>Riverside</a><a href='/about'>About</a></header>"

    suggestion = _suggest(page + b" " * (MAX_INDEX_BYTES - len(page)))

    assert suggestion is not None
    assert suggestion.header is not None
    assert suggestion.header.wordmark == "Riverside"


def test_values_that_fail_the_rules_are_dropped_with_reasons() -> None:
    html = """
    <header>
      <a href="/">Pablo Counseling</a>
      <nav>
        <a href="/about">About</a>
        <a href="mailto:hello@sample-practice.example">Email</a>
        <a href="https://elsewhere.example/">Elsewhere</a>
        <a href="/login">Login</a>
        <a href="javascript:void(0)" class="btn">Book</a>
      </nav>
    </header>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert {s.field: s.reason for s in suggestion.skipped} == {
        "header.wordmark": "Can't be the name of the software the portal runs on.",
        "header.links[1].href": "Must be a page on your website, like /about.",
        "header.links[2].href": "Must be a page on your website, like /about.",
        "header.links[3].label": "Is a word the portal uses itself.",
        "header.cta.href": "Must be a page on your website, like /about.",
    }
    assert suggestion.header == PracticeHeader(links=[HeaderLink(label="About", href="/about")])


def test_the_home_link_is_the_brand_and_a_short_line_beside_it_the_subtitle() -> None:
    html = """
    <body><nav>
      <a href="/about">About the practice</a>
      <div><a href="/">Riverside</a><small>Therapy in Ann Arbor</small></div>
      <a href="/contact">Contact</a>
    </nav></body>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert suggestion.header is not None
    assert suggestion.header.wordmark == "Riverside"
    assert suggestion.header.subtitle == "Therapy in Ann Arbor"
    # No link looks like a button, so the last one is the call to action.
    assert suggestion.header.cta == HeaderLink(label="Contact", href="/contact")
    assert suggestion.header.links == [HeaderLink(label="About the practice", href="/about")]


def test_without_a_home_link_the_longest_link_is_the_brand() -> None:
    html = """
    <header>
      <a href="/a">About</a>
      <a href="/b">Riverside Counseling</a>
      <a href="/c">Contact</a>
    </header>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert suggestion.header is not None
    assert suggestion.header.wordmark == "Riverside Counseling"


def test_scripts_and_styles_are_not_text() -> None:
    html = """
    <header>
      <a href="/"><script>document.write("x")</script>Riverside<style>a{}</style></a>
      <a href="/about">About</a>
    </header>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert suggestion.header is not None
    assert suggestion.header.wordmark == "Riverside"


def test_links_to_the_practices_portal_are_left_out() -> None:
    html = f"""
    <header>
      <a href="/">Riverside</a>
      <a href="/about">About</a>
      <a href="https://{PORTAL}/">Client login</a>
      <a href="/book" class="button-primary">Book</a>
    </header>
    """
    suggestion = _suggest(html)

    assert suggestion is not None
    assert suggestion.header is not None
    assert [link.label for link in suggestion.header.links] == ["About"]
