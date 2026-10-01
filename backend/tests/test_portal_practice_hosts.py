# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own portal hosts: the parts that need no database.

How a ``Host`` value is read, how long an answer is kept, and which address a
portal link gets. The lookups themselves, the public route and the links built
from real rows are proven against Postgres in
``tests_integration/database/test_portal_hosts_db.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.portal import factory, practice_hosts
from app.portal.practice_hosts import PortalHost, PortalHostCache, normalize_request_host
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_STAND_IN_TOKEN = "abc.def.ghi"


@pytest.fixture(autouse=True)
def _clean_settings_and_registrations() -> Iterator[None]:
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    yield
    get_settings.cache_clear()
    factory.reset_delivery_registrations()


# ---------------------------------------------------------------------------
# Reading a Host value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("portal.example.com", "portal.example.com"),
        ("Portal.Example.COM", "portal.example.com"),
        ("portal.example.com:443", "portal.example.com"),
        ("portal.example.com.", "portal.example.com"),
        ("portal.example.com.:8443", "portal.example.com"),
        ("  portal.example.com  ", "portal.example.com"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
    ],
)
def test_a_host_value_reads_as_the_stored_name(raw: str, expected: str) -> None:
    assert normalize_request_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "localhost",
        "203.0.113.7",
        "203.0.113.7:3000",
        "[2001:db8::1]",
        "[2001:db8::1]:443",
        "portal.example.com:https",
        "portal..example.com",
        "-portal.example.com",
        "portal_example.com",
        "portal.example.com/path",
        "user@portal.example.com",
        "a." + "b" * 250 + ".com",
    ],
)
def test_a_value_no_practice_could_hold_reads_as_nothing(raw: str) -> None:
    assert normalize_request_host(raw) is None


# ---------------------------------------------------------------------------
# Keeping answers
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Loader:
    def __init__(self, answers: dict[str, PortalHost | None]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, host: str) -> PortalHost | None:
        self.asked.append(host)
        return self.answers.get(host)


_FOUND = PortalHost(slug="example-therapy", primary_host="portal.example.com")


def test_a_found_host_is_kept_for_the_ttl_then_asked_again() -> None:
    clock = _Clock()
    cache = PortalHostCache(ttl_seconds=60, clock=clock)
    load = _Loader({"portal.example.com": _FOUND})

    assert cache.get_or_load("portal.example.com", load) == _FOUND
    clock.now += 59
    assert cache.get_or_load("portal.example.com", load) == _FOUND
    assert load.asked == ["portal.example.com"]

    clock.now += 1
    cache.get_or_load("portal.example.com", load)
    assert load.asked == ["portal.example.com", "portal.example.com"]


def test_a_host_that_serves_nothing_is_kept_too() -> None:
    """Otherwise a made-up hostname would cost a query on every request."""
    clock = _Clock()
    cache = PortalHostCache(ttl_seconds=60, clock=clock)
    load = _Loader({})

    assert cache.get_or_load("nobody.example.com", load) is None
    assert cache.get_or_load("nobody.example.com", load) is None
    assert load.asked == ["nobody.example.com"]

    clock.now += 60
    assert cache.get_or_load("nobody.example.com", load) is None
    assert load.asked == ["nobody.example.com", "nobody.example.com"]


def test_a_newly_working_host_is_noticed_within_the_ttl() -> None:
    clock = _Clock()
    cache = PortalHostCache(ttl_seconds=60, clock=clock)
    load = _Loader({})

    assert cache.get_or_load("portal.example.com", load) is None
    load.answers["portal.example.com"] = _FOUND
    assert cache.get_or_load("portal.example.com", load) is None

    clock.now += 60
    assert cache.get_or_load("portal.example.com", load) == _FOUND


def test_the_cache_keeps_at_most_its_bound_dropping_the_oldest() -> None:
    clock = _Clock()
    cache = PortalHostCache(ttl_seconds=60, max_entries=2, clock=clock)
    load = _Loader({})

    for host in ("a.example.com", "b.example.com", "c.example.com"):
        cache.get_or_load(host, load)
    load.asked.clear()

    cache.get_or_load("b.example.com", load)
    cache.get_or_load("c.example.com", load)
    assert load.asked == []
    cache.get_or_load("a.example.com", load)
    assert load.asked == ["a.example.com"]


def test_clearing_forgets_every_answer() -> None:
    cache = PortalHostCache(clock=_Clock())
    load = _Loader({"portal.example.com": _FOUND})
    cache.get_or_load("portal.example.com", load)

    cache.clear()
    cache.get_or_load("portal.example.com", load)

    assert load.asked == ["portal.example.com", "portal.example.com"]


# ---------------------------------------------------------------------------
# Where a portal link points
# ---------------------------------------------------------------------------


def _configured(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", base_url)
    get_settings.cache_clear()


def test_a_practice_with_a_working_primary_host_gets_links_at_its_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch, "https://app.example.test")
    asked: list[str] = []

    def primary(slug: str) -> str | None:
        asked.append(slug)
        return "portal.example.com"

    monkeypatch.setattr(practice_hosts, "primary_portal_host_for_slug", primary)

    assert factory.build_portal_link(slug="example-therapy") == "https://portal.example.com/"
    assert factory.build_invite_link(slug="example-therapy", token=_STAND_IN_TOKEN) == (
        "https://portal.example.com/#invite=abc.def.ghi"
    )
    assert asked == ["example-therapy", "example-therapy"]


def test_a_practice_host_needs_no_default_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, "")
    monkeypatch.setattr(
        practice_hosts, "primary_portal_host_for_slug", lambda _slug: "portal.example.com"
    )

    assert factory.portal_page_url("example-therapy") == "https://portal.example.com/"


def test_without_a_working_primary_host_links_use_the_default_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch, "https://app.example.test")
    monkeypatch.setattr(practice_hosts, "primary_portal_host_for_slug", lambda _slug: None)

    assert factory.build_invite_link(slug="example-therapy", token=_STAND_IN_TOKEN) == (
        "https://app.example.test/portal/example-therapy#invite=abc.def.ghi"
    )


def test_a_registered_resolver_still_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    """The practice's host is the default's rule; a deployment's own resolver
    replaces the default whole, and may call the helper itself."""
    _configured(monkeypatch, "https://app.example.test")
    asked: list[str] = []

    def primary(slug: str) -> str | None:
        asked.append(slug)
        return "portal.example.com"

    monkeypatch.setattr(practice_hosts, "primary_portal_host_for_slug", primary)
    factory.register_portal_address_resolver(lambda slug: f"https://clients.example.test/{slug}")

    assert factory.portal_page_url("example-therapy") == (
        "https://clients.example.test/example-therapy"
    )
    assert asked == []
