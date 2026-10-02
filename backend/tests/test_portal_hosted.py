# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's hosted addresses: the parts that need no database.

Which hosts are a hosted address of which slug, which slugs can have one, how
the deployment's hosted domain is read, and that a practice cannot add a host
under it. Resolving hosts to practices, the redirect to a practice's own
primary, and the links built on a hosted address are proven against Postgres
in ``tests_integration/database/test_portal_hosts_db.py`` and
``test_practice_sites_db.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.portal.hosted import (
    HostedPurpose,
    hosted_domain,
    hosted_portal_host,
    hosted_site_host,
    hosted_slug,
    is_under_hosted_domain,
)
from app.portal.slugs import RESERVED_SLUGS, is_dns_label, is_mintable_slug
from app.services.practice_domain_hosts import HostnameError, normalize_host
from app.settings import Settings, get_settings
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

DOMAIN = "hosted.example"


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICE_HOSTED_DOMAIN", DOMAIN)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# The deployment's hosted domain
# ---------------------------------------------------------------------------


def test_unset_gives_no_practice_a_hosted_address() -> None:
    assert hosted_domain() is None
    assert hosted_portal_host("acme") is None
    assert hosted_site_host("acme") is None
    assert hosted_slug("acme.hosted.example", "site") is None
    assert not is_under_hosted_domain("acme.hosted.example")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hosted.example", "hosted.example"),
        ("  Hosted.Example.  ", "hosted.example"),
        ("dev.hosted.example", "dev.hosted.example"),
        ("", ""),
    ],
)
def test_the_hosted_domain_is_read_as_hosts_are_compared(raw: str, expected: str) -> None:
    assert Settings(practice_hosted_domain=raw).practice_hosted_domain == expected


@pytest.mark.parametrize("raw", ["localhost", "https://hosted.example", "*.hosted.example", "a..b"])
def test_a_hosted_domain_that_is_no_domain_stops_the_deployment(raw: str) -> None:
    with pytest.raises(ValidationError, match="PRACTICE_HOSTED_DOMAIN"):
        Settings(practice_hosted_domain=raw)


# ---------------------------------------------------------------------------
# Addresses and the slugs they carry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("hosted")
def test_a_practice_has_a_website_and_a_portal_address() -> None:
    assert hosted_site_host("acme-therapy") == "acme-therapy.hosted.example"
    assert hosted_portal_host("acme-therapy") == "acme-therapy.portal.hosted.example"


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize(
    ("host", "purpose", "slug"),
    [
        ("acme.hosted.example", "site", "acme"),
        ("acme.portal.hosted.example", "portal", "acme"),
        # A portal address is not a website address, and the other way round.
        ("acme.portal.hosted.example", "site", None),
        ("acme.hosted.example", "portal", None),
        # The domain itself, its portal label, and anything deeper are nobody's.
        ("hosted.example", "site", None),
        ("portal.hosted.example", "site", None),
        ("portal.hosted.example", "portal", None),
        ("a.b.hosted.example", "site", None),
        ("a.b.portal.hosted.example", "portal", None),
        # Another domain that merely ends the same way.
        ("acme.nothosted.example", "site", None),
        # Reserved names are never a practice's.
        ("www.hosted.example", "site", None),
        ("status.portal.hosted.example", "portal", None),
    ],
)
def test_a_host_names_a_slug_only_where_it_is_that_address(
    host: str, purpose: HostedPurpose, slug: str | None
) -> None:
    assert hosted_slug(host, purpose) == slug


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("slug", ["www", "mail", "api", "portal", "Acme", "acme_therapy", "-acme"])
def test_a_slug_that_is_reserved_or_no_label_has_no_hosted_address(slug: str) -> None:
    assert hosted_site_host(slug) is None
    assert hosted_portal_host(slug) is None


@pytest.mark.usefixtures("hosted")
def test_hosts_under_the_hosted_domain_are_recognised() -> None:
    assert is_under_hosted_domain("hosted.example")
    assert is_under_hosted_domain("anything.portal.hosted.example")
    assert not is_under_hosted_domain("nothosted.example")


# ---------------------------------------------------------------------------
# Which slugs can be minted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "www",
        "app",
        "api",
        "portal",
        "sites",
        "dev",
        "staging",
        "mail",
        "email",
        "smtp",
        "notifications",
        "launch",
        "admin",
        "login",
        "signin",
        "auth",
        "support",
        "help",
        "billing",
        "status",
        "security",
        "docs",
        "blog",
    ],
)
def test_names_a_hosted_domain_would_use_itself_are_reserved(name: str) -> None:
    assert name in RESERVED_SLUGS
    assert not is_mintable_slug(name)


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("acme", True),
        ("acme-therapy-2", True),
        ("a" * 63, True),
        ("a" * 64, False),
        ("-acme", False),
        ("acme-", False),
        ("Acme", False),
        ("acme.therapy", False),
        ("", False),
    ],
)
def test_a_slug_is_a_dns_label(name: str, label: bool) -> None:
    assert is_dns_label(name) is label


def test_a_slug_is_at_least_three_characters() -> None:
    assert not is_mintable_slug("ab")
    assert is_mintable_slug("abc")


# ---------------------------------------------------------------------------
# A practice cannot add a host under the hosted domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["hosted.example", "acme.hosted.example", "x.portal.hosted.example"]
)
def test_a_host_under_a_reserved_domain_is_refused(raw: str) -> None:
    with pytest.raises(HostnameError, match="part of this service"):
        normalize_host(raw, reserved_domains=frozenset({DOMAIN}))


def test_a_domain_that_only_ends_the_same_way_is_a_practices_to_add() -> None:
    assert (
        normalize_host("clinic.nothosted.example", reserved_domains=frozenset({DOMAIN}))
        == "clinic.nothosted.example"
    )
