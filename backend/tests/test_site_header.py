# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``header`` block of a website's ``theme.json``: what the portal's
header takes from it, what it skips and why.

The reasons are asserted word for word: they are what whoever made the website
reads in Settings > Website. The frontend's second check of the same values is
pinned in ``frontend/src/lib/portal-host/__tests__/practice-header.test.ts``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.settings import get_settings
from app.sites.header import HeaderLink, PracticeHeader, for_hosts
from app.sites.theme import ThemeReport, read_theme, storable_theme, stored_theme

HOSTS = frozenset({"riverside.example", "www.riverside.example", "portal.riverside.example"})
BRANDS = ("Pablo",)

HEADER = {
    "wordmark": "Riverside Counseling",
    "subtitle": "Individual and couples therapy",
    "links": [
        {"label": "Services", "href": "/#services"},
        {"label": "About", "href": "/about"},
        {"label": "Fees", "href": "https://www.riverside.example/fees?plan=self-pay"},
    ],
    "cta": {"label": "Schedule a visit", "href": "/#schedule"},
}

NOT_A_PAGE = "Must be a page on your website, like /about."
INVISIBLE = "Contains a control or invisible character."
MIXED = "Mixes alphabets in a way browsers warn about."
BRAND = "Can't be the name of the software the portal runs on."
RESERVED = "Is a word the portal uses itself."


def _read(header: Any, hosts: frozenset[str] = HOSTS) -> ThemeReport:
    report = read_theme(json.dumps({"version": 1, "header": header}).encode(), hosts, BRANDS)
    assert report is not None
    return report


def _skipped(report: ThemeReport) -> dict[str, str]:
    return {s.field: s.reason for s in report.skipped}


def _header(report: ThemeReport) -> PracticeHeader:
    assert report.theme is not None
    assert report.theme.header is not None
    return report.theme.header


def _with(**changes: Any) -> dict[str, Any]:
    return {**HEADER, **changes}


def test_a_full_header_applies_whole() -> None:
    report = _read(HEADER)

    assert report.skipped == []
    assert _header(report) == PracticeHeader.model_validate(HEADER)


def test_a_header_alone_is_a_theme_and_is_stored_with_it() -> None:
    theme = _read(HEADER).theme

    assert theme is not None
    stored = storable_theme(theme)
    assert stored == {"version": 1, "colors": {}, "fonts": {}, "header": HEADER}
    assert stored_theme(stored) == theme


def test_a_theme_without_a_header_stores_as_it_did() -> None:
    report = read_theme(b'{"radius": "md"}', HOSTS, BRANDS)

    assert report is not None
    assert storable_theme(report.theme) == {"version": 1, "colors": {}, "fonts": {}, "radius": "md"}


def test_the_report_round_trips_through_its_json_form() -> None:
    report = _read(_with(subtitle=""))

    assert ThemeReport.model_validate(report.model_dump(mode="json")) == report


def test_a_header_that_is_not_an_object_is_skipped_whole() -> None:
    report = _read(["Riverside"])

    assert _skipped(report) == {
        "header": 'Should be an object, like {"wordmark": "Your practice"}.'
    }
    assert report.theme is None


def test_text_is_normalised() -> None:
    header = _header(
        _read(_with(wordmark="  Riverside\u3000  Counseling ", subtitle="\ufb01ne care"))
    )

    assert header.wordmark == "Riverside Counseling"
    # NFKC: the ligature becomes two letters.
    assert header.subtitle == "fine care"


@pytest.mark.parametrize(
    "text",
    [
        "Riverside\x00",  # C0
        "Riverside\x85",  # C1
        "Riverside\x1b[31m",  # escape
        "Riverside\nCounseling",  # newline is a control too
        "River\u202eside",  # right-to-left override
        "River\u2066side\u2069",  # isolate
        "River\u200fside",  # right-to-left mark
        "River\u061cside",  # Arabic letter mark
        "River\u200bside",  # zero-width space (Cf)
        "River\u00adside",  # soft hyphen (Cf)
        "River\ue000side",  # private use
        "River\U000e0001side",  # language tag (Cf)
        "River\u2028side",  # line separator
        "River\u2029side",  # paragraph separator
        "River\U0010fffeside",  # unassigned (noncharacter)
    ],
)
def test_controls_and_invisible_characters_are_refused(text: str) -> None:
    assert _skipped(_read(_with(wordmark=text))) == {"header.wordmark": INVISIBLE}


@pytest.mark.parametrize(
    "text",
    [
        "Riv\u0435rside",  # a Cyrillic e among Latin letters
        "\u03a1iverside",  # Greek capital rho
        "Riverside \u0421\u043eunseling",  # one mixed word is enough
    ],
)
def test_a_word_that_mixes_alphabets_is_refused(text: str) -> None:
    assert _skipped(_read(_with(subtitle=text))) == {"header.subtitle": MIXED}


@pytest.mark.parametrize(
    "text",
    [
        "Riverside თერაპია",  # two alphabets, one per word
        "東京カウンセリング",  # Japanese: kanji and katakana together
        "ひだまり心療内科",  # Japanese: hiragana and kanji together
        "서울상담센터",  # Korean
        "Café Ünïcödé",  # Latin with marks
        "Riverside 2.0 - Care & Co.",  # digits and punctuation belong to every alphabet
        "O\u2019Brien Counseling",
    ],
)
def test_one_alphabet_a_word_passes(text: str) -> None:
    assert _read(_with(wordmark=text)).skipped == []


def test_empty_text_is_refused() -> None:
    assert _skipped(_read(_with(wordmark="   ", subtitle=""))) == {
        "header.wordmark": "Is empty.",
        "header.subtitle": "Is empty.",
    }


def test_text_that_is_not_a_string_is_refused() -> None:
    assert _skipped(_read(_with(wordmark=7))) == {"header.wordmark": "Should be text."}


def test_text_is_capped_in_characters_after_normalising() -> None:
    report = _read(
        _with(
            wordmark="W" * 61,
            subtitle="S" * 81,
            links=[{"label": "L" * 25, "href": "/a"}],
            cta={"label": "C" * 24, "href": "/b"},
        )
    )

    assert _skipped(report) == {
        "header.wordmark": "Is longer than 60 characters.",
        "header.subtitle": "Is longer than 80 characters.",
        "header.links[0].label": "Is longer than 24 characters.",
    }
    assert _header(report).cta == HeaderLink(label="C" * 24, href="/b")
    # Sixty characters, once the extra spaces have gone.
    assert _read(_with(wordmark=" ".join(["W" * 29, "W" * 30]) + "    ")).skipped == []


@pytest.mark.parametrize("label", ["Sign in", "log in", "LOGIN", "Sign  out", "pablo"])
def test_a_label_may_not_be_the_portals_own_words(label: str) -> None:
    report = _read(_with(links=[{"label": label, "href": "/a"}]))

    reason = BRAND if label == "pablo" else RESERVED
    assert _skipped(report) == {"header.links[0].label": reason}


def test_a_longer_label_that_contains_those_words_passes() -> None:
    assert _read(_with(cta={"label": "Client login", "href": "/login"})).skipped == []


@pytest.mark.parametrize("wordmark", ["Pablo", "PABLO Health", "Pablo\u2019s Portal"])
def test_the_wordmark_may_not_be_or_start_with_the_brand(wordmark: str) -> None:
    assert _skipped(_read(_with(wordmark=wordmark))) == {"header.wordmark": BRAND}


def test_the_brand_list_is_the_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "portal_header_brand_names", "Acme Care, Acme")
    report = read_theme(
        json.dumps({"header": {"wordmark": "Acme Care Portal", "subtitle": "Pablo"}}).encode(),
        HOSTS,
    )

    assert report is not None
    assert _skipped(report) == {"header.wordmark": BRAND}


@pytest.mark.parametrize(
    "href",
    [
        "/",
        "/about",
        "/#services",
        "/services/therapy?for=couples#fees",
        "/a%20page",
        "/100%25-online",
        "/%zz",
        "https://riverside.example",
        "https://riverside.example/",
        "https://WWW.Riverside.Example./about",
        "https://portal.riverside.example/messages",
        "HTTPS://riverside.example/about",
    ],
)
def test_pages_on_the_website_are_links(href: str) -> None:
    report = _read(_with(cta={"label": "Go", "href": href}))

    assert report.skipped == []
    assert _header(report).cta == HeaderLink(label="Go", href=href)


@pytest.mark.parametrize(
    "href",
    [
        "about",  # relative to nothing
        "#services",
        "riverside.example/about",  # a bare hostname
        "//riverside.example/about",  # scheme-relative
        "/\\evil.example",  # a backslash browsers read as a slash
        "/../admin",
        "/a/../../b",
        "/%2e%2e/admin",  # .. once decoded
        "/%2F%2Fevil.example",  # // once decoded
        "/a b",
        "/a\tb",
        "/a%0Ab",  # a newline once decoded
        "http://riverside.example/",
        "https://evil.example/",
        "https://riverside.example.evil.example/",
        "https://user@riverside.example/",
        "https://riverside.example:8443/",
        "https:///about",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "mailto:hello@riverside.example",
        "tel:+15555550100",
        "blob:https://riverside.example/1234",
        "",
        "/" + "a" * 512,
    ],
)
def test_anything_else_is_not_a_link(href: str) -> None:
    report = _read(_with(cta={"label": "Go", "href": href}))

    reason = "Is longer than 512 characters." if len(href) > 512 else NOT_A_PAGE
    assert _skipped(report) == {"header.cta.href": reason}
    assert _header(report).cta is None


def test_without_hosts_only_paths_are_links() -> None:
    report = _read(HEADER, hosts=frozenset())

    assert _skipped(report) == {"header.links[2].href": NOT_A_PAGE}


def test_a_link_missing_its_label_or_href_is_skipped_whole() -> None:
    links = [{"label": "About"}, {"href": "/about"}, "About", {"label": "Fees", "href": "/fees"}]
    report = _read(_with(links=links))

    assert _skipped(report) == {
        "header.links[0]": "Needs a label and an href.",
        "header.links[1]": "Needs a label and an href.",
        "header.links[2]": "Needs a label and an href.",
    }
    assert _header(report).links == [HeaderLink(label="Fees", href="/fees")]


def test_a_link_with_one_bad_part_is_dropped_with_the_reason() -> None:
    report = _read(_with(links=[{"label": "Services", "href": "javascript:void(0)"}]))

    assert _skipped(report) == {"header.links[0].href": NOT_A_PAGE}
    assert _header(report).links == []


def test_at_most_five_links() -> None:
    links = [{"label": f"Page {i}", "href": f"/{i}"} for i in range(7)]
    report = _read(_with(links=links))

    assert _skipped(report) == {
        "header.links[5]": "The portal shows at most 5 links.",
        "header.links[6]": "The portal shows at most 5 links.",
    }
    assert [link.href for link in _header(report).links] == ["/0", "/1", "/2", "/3", "/4"]


def test_a_failed_link_does_not_use_up_one_of_the_five() -> None:
    links = [{"label": "Bad", "href": "javascript:x"}] + [
        {"label": f"Page {i}", "href": f"/{i}"} for i in range(5)
    ]

    assert len(_header(_read(_with(links=links))).links) == 5


def test_a_second_link_to_the_same_page_is_skipped() -> None:
    report = _read(
        _with(links=[{"label": "About", "href": "/about"}, {"label": "Me", "href": "/about"}])
    )

    assert _skipped(report) == {"header.links[1].href": "Goes to the same page as an earlier link."}


def test_links_that_are_not_a_list_are_skipped() -> None:
    report = _read(_with(links={"label": "About", "href": "/about"}))

    assert _skipped(report) == {
        "header.links": 'Should be a list, like [{"label": "About", "href": "/about"}].'
    }


def test_the_call_to_action_needs_both_parts_to_pass() -> None:
    report = _read(_with(cta={"label": "Sign in", "href": "https://evil.example/"}))

    assert _skipped(report) == {"header.cta.label": RESERVED, "header.cta.href": NOT_A_PAGE}
    assert _header(report).cta is None


def test_a_header_with_nothing_usable_is_no_header() -> None:
    report = _read({"wordmark": "", "links": [{"label": "x", "href": "javascript:x"}]})

    assert report.theme is None
    assert set(_skipped(report)) == {"header.wordmark", "header.links[0].href"}


def test_unknown_header_keys_are_ignored() -> None:
    report = _read(_with(logo="img/mark.png", html="<b>hi</b>"))

    assert report.skipped == []
    assert _header(report) == PracticeHeader.model_validate(HEADER)


def test_full_addresses_on_hosts_the_practice_no_longer_holds_are_dropped() -> None:
    header = PracticeHeader.model_validate(
        _with(cta={"label": "Book", "href": "https://portal.riverside.example/book"})
    )

    kept = for_hosts(header, frozenset({"riverside.example"}))

    assert kept is not None
    assert [link.href for link in kept.links] == ["/#services", "/about"]
    assert kept.cta is None
    assert for_hosts(header, HOSTS) == header


def test_a_header_left_with_nothing_is_none() -> None:
    header = PracticeHeader(links=[HeaderLink(label="Fees", href="https://riverside.example/fees")])

    assert for_hosts(header, frozenset()) is None
