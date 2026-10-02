# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's hosted addresses against real PostgreSQL.

With a hosted domain configured, every practice with a portal address has
``{slug}.portal.{domain}`` for its portal and ``{slug}.{domain}`` for its
website. Proven here on committed rows, through the code's own sessions:

* the portal host route answers the hosted portal address like a working host
  of the practice's own, naming the practice's working primary when it has one
  so visitors are sent there, and nothing while the portal is off;
* the website host route answers the hosted website address once a version is
  live, names the practice's working primary website host when it has one, and
  names the portal host that ``/portal`` there is sent to;
* portal links prefer the practice's working primary portal host, then its
  hosted portal address, then the shared portal address;
* Settings > Domains and Settings > Website name the hosted addresses, minting
  the practice's portal address first when it has none;
* a slug that is reserved has no hosted address, and nothing at all answers
  while no hosted domain is configured.

The parsing of hosts and slugs is unit-tested in ``tests/test_portal_hosted.py``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from app.db import create_standalone_session
from app.portal import factory, host_routes
from app.portal.practice_hosts import PortalHostCache, get_portal_host_cache
from app.routes import practice_domains
from app.services.file_storage import LocalFileStorage
from app.settings import get_settings
from app.sites import public_routes
from app.sites.hosts import SiteHostCache, get_site_host_cache
from app.sites.service import PracticeSiteService
from app.sites.store import PracticeSiteStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

DOMAIN = "hosted.example"
# Not a credential: three dotted words in the shape of one.
_STAND_IN_INVITE = "abc.def.ghi"


@dataclass
class _Rows:
    """Every platform row a test wrote, so it can all be taken out again."""

    engine: Engine
    hosts: list[str] = field(default_factory=list)
    practices: list[str] = field(default_factory=list)

    def practice(
        self, *, slug: str | None = None, portal: bool = True, minted: bool = True
    ) -> tuple[str, str]:
        """A practice with the portal on or off, and a portal address unless
        *minted* is false. Returns (id, slug)."""
        practice_id = f"hosted-{uuid.uuid4().hex[:10]}"
        practice_slug = slug or f"hosted-{uuid.uuid4().hex[:10]}"
        self.practices.append(practice_id)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practices "
                    "(id, name, schema_name, owner_email, product, status, is_active, created_at) "
                    "VALUES (:p, 'Example Therapy', :s, 'owner@example.com', 'pablo', 'active', "
                    "true, now())"
                ),
                {"p": practice_id, "s": f"practice_{practice_id.replace('-', '_')}"},
            )
            if minted:
                conn.execute(
                    text(
                        "INSERT INTO platform.companion_practice_slugs "
                        "(slug, practice_id, display_name, created_at) "
                        "VALUES (:s, :p, 'Example Therapy', now())"
                    ),
                    {"s": practice_slug, "p": practice_id},
                )
            conn.execute(
                text(
                    "INSERT INTO platform.practice_portal_settings "
                    "(practice_id, enabled, decided_at, updated_at) "
                    "VALUES (:p, :e, now(), now())"
                ),
                {"p": practice_id, "e": portal},
            )
        return practice_id, practice_slug

    def host(
        self, practice_id: str, *, purpose: str, primary: bool = True, status: str = "active"
    ) -> str:
        host = f"{purpose}.{uuid.uuid4().hex[:10]}.example.com"
        self.hosts.append(host)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domains "
                    "(domain, practice_id, purpose, kind, status, is_primary, created_at) "
                    "VALUES (:d, :p, :purpose, 'vanity', :status, :primary, now())"
                ),
                {
                    "d": host,
                    "p": practice_id,
                    "purpose": purpose,
                    "status": status,
                    "primary": primary,
                },
            )
        return host

    def site(self, practice_id: str, *, live: bool = True) -> None:
        """A website with one version, live or not."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_sites "
                    "(practice_id, live_version, next_version, updated_at) "
                    "VALUES (:p, :live, 2, now())"
                ),
                {"p": practice_id, "live": 1 if live else None},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.practice_site_versions "
                    "(practice_id, version, file_count, total_bytes, published_at, published_by) "
                    "VALUES (:p, 1, 1, 1, now(), 'publisher')"
                ),
                {"p": practice_id},
            )

    def slug_of(self, practice_id: str) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(
                text("SELECT slug FROM platform.companion_practice_slugs WHERE practice_id = :p"),
                {"p": practice_id},
            ).scalar_one_or_none()

    def remove_all(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM platform.practice_domains WHERE domain = ANY(:h)"),
                {"h": self.hosts},
            )
            for table in (
                "practice_site_versions",
                "practice_sites",
                "companion_practice_slugs",
                "practice_portal_settings",
            ):
                conn.execute(
                    text(f"DELETE FROM platform.{table} WHERE practice_id = ANY(:p)"),  # noqa: S608 — fixed table names
                    {"p": self.practices},
                )
            conn.execute(
                text("DELETE FROM platform.practices WHERE id = ANY(:p)"), {"p": self.practices}
            )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def rows(engine: Engine) -> Iterator[_Rows]:
    written = _Rows(engine)
    yield written
    written.remove_all()


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A hosted domain, websites on, the shared portal address, no resolver."""
    monkeypatch.setenv("PRACTICE_HOSTED_DOMAIN", DOMAIN)
    monkeypatch.setenv("PRACTICE_HOSTED_DOMAIN_READY", "true")
    monkeypatch.setenv("PRACTICE_SITE_BUCKET", "hosted-sites")
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    yield
    factory.reset_delivery_registrations()
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(host_routes.router)
    app.include_router(public_routes.router)
    portal_hosts = PortalHostCache()
    site_hosts = SiteHostCache()
    app.dependency_overrides[get_portal_host_cache] = lambda: portal_hosts
    app.dependency_overrides[get_site_host_cache] = lambda: site_hosts
    with TestClient(app) as test_client:
        yield test_client


def _portal(client: TestClient, host: str) -> Any:
    return client.get(f"/api/portal/hosts/{host}")


def _site(client: TestClient, host: str) -> Any:
    return client.get(f"/api/sites/hosts/{host}")


# ---------------------------------------------------------------------------
# The portal on its hosted address
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("hosted")
def test_the_hosted_portal_address_serves_the_practices_portal(
    client: TestClient, rows: _Rows
) -> None:
    _, slug = rows.practice()

    response = _portal(client, f"{slug}.portal.{DOMAIN}")

    assert response.status_code == 200
    assert response.json() == {
        "slug": slug,
        "primary_host": None,
        "theme": None,
        "site_host": None,
    }


@pytest.mark.usefixtures("hosted")
def test_the_portal_links_back_to_the_hosted_website_once_one_is_live(
    client: TestClient, rows: _Rows
) -> None:
    practice_id, slug = rows.practice()
    rows.site(practice_id)

    assert _portal(client, f"{slug}.portal.{DOMAIN}").json()["site_host"] == f"{slug}.{DOMAIN}"

    own = rows.host(practice_id, purpose="site")
    portal_host = rows.host(practice_id, purpose="portal")
    assert _portal(client, portal_host).json()["site_host"] == own


@pytest.mark.usefixtures("hosted")
def test_the_hosted_portal_address_sends_visitors_to_a_working_primary_of_the_practices_own(
    client: TestClient, rows: _Rows
) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, purpose="portal")
    other_id, other_slug = rows.practice()
    rows.host(other_id, purpose="portal", status="pending")

    assert _portal(client, f"{slug}.portal.{DOMAIN}").json()["primary_host"] == primary
    # A primary that does not work yet sends nobody anywhere.
    assert _portal(client, f"{other_slug}.portal.{DOMAIN}").json()["primary_host"] is None


@pytest.mark.usefixtures("hosted")
def test_a_hosted_portal_address_with_nothing_to_serve_is_the_same_404(
    client: TestClient, rows: _Rows
) -> None:
    _, off = rows.practice(portal=False)
    _, on = rows.practice()

    for host in (
        f"{off}.portal.{DOMAIN}",
        f"nobody-{uuid.uuid4().hex[:8]}.portal.{DOMAIN}",
        # The website address is not a portal address.
        f"{on}.{DOMAIN}",
        f"portal.{DOMAIN}",
    ):
        assert _portal(client, host).status_code == 404, host


def test_without_a_hosted_domain_nothing_answers_on_one(
    client: TestClient, rows: _Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRACTICE_SITE_BUCKET", "hosted-sites")
    get_settings.cache_clear()
    practice_id, slug = rows.practice()
    rows.site(practice_id)
    try:
        assert _portal(client, f"{slug}.portal.{DOMAIN}").status_code == 404
        assert _site(client, f"{slug}.{DOMAIN}").status_code == 404
    finally:
        get_settings.cache_clear()


def test_a_domain_not_yet_served_resolves_but_is_shown_to_nobody(
    client: TestClient, rows: _Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named but not marked ready: the hosted hosts answer as they will, but
    Settings shows none, links stay on the shared address and the portal links
    back to no hosted website, so nobody is sent to a name not yet served."""
    monkeypatch.setenv("PRACTICE_HOSTED_DOMAIN", DOMAIN)
    monkeypatch.setenv("PRACTICE_SITE_BUCKET", "hosted-sites")
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    practice_id, slug = rows.practice()
    rows.site(practice_id)
    session = create_standalone_session()
    try:
        portal = _portal(client, f"{slug}.portal.{DOMAIN}")
        assert portal.status_code == 200
        assert portal.json()["site_host"] is None
        assert _site(client, f"{slug}.{DOMAIN}").status_code == 200

        assert practice_domains._hosted(practice_id) is None
        assert factory.portal_page_url(slug) == f"https://app.example.test/portal/{slug}"
        service = PracticeSiteService(PracticeSiteStore(session), LocalFileStorage(), "b")
        assert service.status(practice_id).live_host is None
    finally:
        session.close()
        get_settings.cache_clear()


@pytest.mark.usefixtures("hosted")
def test_a_reserved_slug_has_no_hosted_address(client: TestClient, rows: _Rows) -> None:
    """A practice that holds a name reserved after it was given keeps it as its
    portal address, but gets nothing under the hosted domain."""
    practice_id, _ = rows.practice(slug="status")
    rows.site(practice_id)

    assert _portal(client, f"status.portal.{DOMAIN}").status_code == 404
    assert _site(client, f"status.{DOMAIN}").status_code == 404
    assert practice_domains._hosted(practice_id) is None


# ---------------------------------------------------------------------------
# The website on its hosted address
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("hosted")
def test_the_hosted_website_address_serves_once_a_version_is_live(
    client: TestClient, rows: _Rows
) -> None:
    published, slug = rows.practice()
    rows.site(published)
    unpublished, unpublished_slug = rows.practice()
    rows.site(unpublished, live=False)

    assert _site(client, f"{slug}.{DOMAIN}").json() == {
        "primary_host": None,
        "portal_host": f"{slug}.portal.{DOMAIN}",
    }
    assert _site(client, f"{unpublished_slug}.{DOMAIN}").status_code == 404
    # The portal address is not a website address.
    assert _site(client, f"{slug}.portal.{DOMAIN}").status_code == 404


@pytest.mark.usefixtures("hosted")
def test_the_hosted_website_address_names_the_practices_own_primaries(
    client: TestClient, rows: _Rows
) -> None:
    practice_id, slug = rows.practice()
    rows.site(practice_id)
    site_primary = rows.host(practice_id, purpose="site")
    portal_primary = rows.host(practice_id, purpose="portal")

    assert _site(client, f"{slug}.{DOMAIN}").json() == {
        "primary_host": site_primary,
        "portal_host": portal_primary,
    }
    # On the practice's own website host, /portal is the website's.
    assert _site(client, site_primary).json() == {"primary_host": site_primary, "portal_host": None}


# ---------------------------------------------------------------------------
# Where portal links point
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("hosted")
def test_links_prefer_the_working_primary_then_the_hosted_address(rows: _Rows) -> None:
    with_primary, primary_slug = rows.practice()
    primary = rows.host(with_primary, purpose="portal")
    with_pending, pending_slug = rows.practice()
    rows.host(with_pending, purpose="portal", status="pending")

    assert factory.portal_page_url(primary_slug) == f"https://{primary}/"
    assert factory.portal_page_url(pending_slug) == f"https://{pending_slug}.portal.{DOMAIN}/"
    assert factory.build_invite_link(slug=pending_slug, token=_STAND_IN_INVITE) == (
        f"https://{pending_slug}.portal.{DOMAIN}/#invite=abc.def.ghi"
    )


def test_links_use_the_shared_address_with_no_hosted_domain(
    rows: _Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    _, slug = rows.practice()
    try:
        assert factory.portal_page_url(slug) == f"https://app.example.test/portal/{slug}"
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("hosted")
def test_a_reserved_slug_keeps_the_shared_address(rows: _Rows) -> None:
    rows.practice(slug="support")

    assert factory.portal_page_url("support") == "https://app.example.test/portal/support"


# ---------------------------------------------------------------------------
# What Settings shows
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("hosted")
def test_domains_names_the_hosted_addresses_and_what_they_serve(rows: _Rows) -> None:
    practice_id, slug = rows.practice(portal=False)
    rows.site(practice_id, live=False)

    hosted = practice_domains._hosted(practice_id)

    assert hosted is not None
    assert hosted.model_dump() == {
        "portal_host": f"{slug}.portal.{DOMAIN}",
        "portal_on": False,
        "site_host": f"{slug}.{DOMAIN}",
        "site_live": False,
    }


@pytest.mark.usefixtures("hosted")
def test_domains_gives_a_practice_its_portal_address_first(rows: _Rows) -> None:
    practice_id, _ = rows.practice(minted=False)

    hosted = practice_domains._hosted(practice_id)

    slug = rows.slug_of(practice_id)
    assert slug is not None
    assert hosted is not None
    assert hosted.site_host == f"{slug}.{DOMAIN}"
    assert hosted.portal_on is True


def test_domains_names_nothing_with_no_hosted_domain(rows: _Rows) -> None:
    get_settings.cache_clear()
    practice_id, _ = rows.practice()

    assert practice_domains._hosted(practice_id) is None


@pytest.mark.usefixtures("hosted")
def test_the_website_is_live_at_its_hosted_address_until_the_practice_has_its_own(
    rows: _Rows,
) -> None:

    practice_id, slug = rows.practice()
    rows.site(practice_id)
    session = create_standalone_session()
    try:
        service = PracticeSiteService(PracticeSiteStore(session), LocalFileStorage(), "b")
        status = service.status(practice_id)
        assert status.live_host == f"{slug}.{DOMAIN}"
        assert status.has_active_host is True

        own = rows.host(practice_id, purpose="site")
        assert service.status(practice_id).live_host == own
    finally:
        session.close()
