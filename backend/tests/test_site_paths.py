# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which file of a website a request path names, and what a missing one gets."""

from __future__ import annotations

import pytest
from app.sites.paths import SitePath, resolve_site_path

FILES = frozenset(
    {"index.html", "about/index.html", "css/site.css", "my page.html", "404.html", "docs/a.pdf"}
)


@pytest.mark.parametrize(
    ("request_path", "expected"),
    [
        ("/", SitePath(200, "index.html")),
        ("", SitePath(200, "index.html")),
        ("/about/", SitePath(200, "about/index.html")),
        ("/css/site.css", SitePath(200, "css/site.css")),
        ("/my%20page.html", SitePath(200, "my page.html")),
        ("/docs/a.pdf", SitePath(200, "docs/a.pdf")),
        ("/about", SitePath(301, location="about/")),
        ("/nope.html", SitePath(404, "404.html")),
        ("/docs/", SitePath(404, "404.html")),
        ("/css/", SitePath(404, "404.html")),
    ],
)
def test_request_paths(request_path: str, expected: SitePath) -> None:
    assert resolve_site_path(request_path, FILES) == expected


@pytest.mark.parametrize(
    "attempt",
    [
        "/../index.html",
        "/%2e%2e/index.html",
        "/css/../index.html",
        "/css/..%2findex.html",
        "/./index.html",
        "//index.html",
        "/css\\site.css",
        "/index.html%00",
        "/%0aindex.html",
    ],
)
def test_traversal_and_odd_paths_are_missing(attempt: str) -> None:
    assert resolve_site_path(attempt, FILES) == SitePath(404, "404.html")


def test_missing_without_a_404_page_is_a_plain_404() -> None:
    assert resolve_site_path("/nope", frozenset({"index.html"})) == SitePath(404)


def test_a_folder_redirect_keeps_its_name_encoded() -> None:
    files = frozenset({"index.html", "our team/index.html"})
    assert resolve_site_path("/our%20team", files) == SitePath(301, location="our%20team/")
