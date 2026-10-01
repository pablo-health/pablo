# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own portal hosts against real PostgreSQL.

Two things read ``platform.practice_domains`` here, and both are proven on
committed rows through the code's own sessions:

* ``GET /api/portal/hosts/{host}`` — the public route the web app asks before
  it serves a request on a host it does not otherwise know. Only an active
  portal host of a practice that offers the portal answers; everything else is
  one 404. An alias names the primary it should send visitors to.
* The default portal address — a link goes to the root of the practice's
  primary portal host while that host is active, and to the shared portal
  address otherwise; a registered resolver still decides, and can ask the same
  question through ``primary_portal_host_for_slug``.

The answer-keeping and the reading of a ``Host`` value are unit-tested in
``tests/test_portal_practice_hosts.py``; here the cache is only shown to sit in
front of the database.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from app.portal import factory, host_routes
from app.portal.practice_hosts import (
    PortalHostCache,
    get_portal_host_cache,
    portal_host_root_url,
    primary_portal_host_for_slug,
)
from app.settings import get_settings
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

URL = "/api/portal/hosts/{host}"
# Not a credential: three dotted words in the shape of one.
_STAND_IN_INVITE = "abc.def.ghi"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _Rows:
    """Every platform row a test wrote, so it can all be taken out again."""

    engine: Engine
    hosts: list[str] = field(default_factory=list)
    practices: list[str] = field(default_factory=list)

    def practice(self, *, slug: bool = True, portal: bool | None = True) -> tuple[str, str]:
        """A practice with a portal address (unless *slug* is false) and the
        portal on, off, or never decided (``None``). Returns (id, slug)."""
        practice_id = f"portal-hosts-{uuid.uuid4().hex[:10]}"
        practice_slug = f"hosts-{uuid.uuid4().hex[:10]}"
        self.practices.append(practice_id)
        with self.engine.begin() as conn:
            if slug:
                conn.execute(
                    text(
                        "INSERT INTO platform.companion_practice_slugs "
                        "(slug, practice_id, display_name, created_at) "
                        "VALUES (:s, :p, 'Example Therapy', now())"
                    ),
                    {"s": practice_slug, "p": practice_id},
                )
            if portal is not None:
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
        self,
        practice_id: str,
        *,
        status: str = "active",
        purpose: str = "portal",
        primary: bool = False,
        prefix: str = "portal",
    ) -> str:
        host = f"{prefix}.{uuid.uuid4().hex[:10]}.example.com"
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

    def set_status(self, host: str, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE platform.practice_domains SET status = :s WHERE domain = :d"),
                {"s": status, "d": host},
            )

    def remove_all(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM platform.practice_domains WHERE domain = ANY(:h)"),
                {"h": self.hosts},
            )
            for table in ("companion_practice_slugs", "practice_portal_settings"):
                conn.execute(
                    text(f"DELETE FROM platform.{table} WHERE practice_id = ANY(:p)"),  # noqa: S608 — fixed table names
                    {"p": self.practices},
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
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def client(clock: _Clock) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(host_routes.router)
    cache = PortalHostCache(clock=clock)
    app.dependency_overrides[get_portal_host_cache] = lambda: cache
    with TestClient(app) as test_client:
        yield test_client


def _ask(client: TestClient, host: str) -> Any:
    return client.get(URL.format(host=host))


# ---------------------------------------------------------------------------
# The public route
# ---------------------------------------------------------------------------


def test_the_primary_host_serves_its_practice(client: TestClient, rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, primary=True)

    response = _ask(client, primary)

    assert response.status_code == 200
    assert response.json() == {"slug": slug, "primary_host": primary}


def test_an_alias_names_the_primary_to_send_visitors_to(client: TestClient, rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, primary=True)
    alias = rows.host(practice_id, prefix="clients")

    response = _ask(client, alias)

    assert response.status_code == 200
    assert response.json() == {"slug": slug, "primary_host": primary}


def test_a_practice_with_no_working_primary_serves_on_every_active_host(
    client: TestClient, rows: _Rows
) -> None:
    """No primary, or a primary that has stopped working: there is nowhere
    better to send anyone, so each active host serves the portal itself."""
    practice_id, slug = rows.practice()
    alias = rows.host(practice_id)
    assert _ask(client, alias).json() == {"slug": slug, "primary_host": None}

    other_id, other_slug = rows.practice()
    rows.host(other_id, primary=True, status="error")
    other_alias = rows.host(other_id, prefix="clients")
    assert _ask(client, other_alias).json() == {"slug": other_slug, "primary_host": None}


def test_a_host_value_is_read_as_a_host_header_would_be(client: TestClient, rows: _Rows) -> None:
    practice_id, _slug = rows.practice()
    primary = rows.host(practice_id, primary=True)

    for spelling in (primary.upper(), f"{primary}:443", f"{primary}."):
        assert _ask(client, spelling).status_code == 200, spelling


def test_every_host_that_serves_no_portal_is_the_same_404(client: TestClient, rows: _Rows) -> None:
    """Not working yet, failing, a website, not a practice's portal at all —
    one answer, so the route cannot be asked which is which."""
    practice_id, _slug = rows.practice()
    rows.host(practice_id, primary=True)
    not_serving = [
        rows.host(practice_id, status="pending"),
        rows.host(practice_id, status="verifying"),
        rows.host(practice_id, status="error"),
        rows.host(practice_id, purpose="site", prefix="www"),
        f"unknown.{uuid.uuid4().hex[:10]}.example.com",
        "203.0.113.7",
        "localhost",
    ]
    no_address_id, _ = rows.practice(slug=False)
    not_serving.append(rows.host(no_address_id, primary=True))
    portal_off_id, _ = rows.practice(portal=False)
    not_serving.append(rows.host(portal_off_id, primary=True))
    never_decided_id, _ = rows.practice(portal=None)
    not_serving.append(rows.host(never_decided_id, primary=True))

    answers = {host: _ask(client, host) for host in not_serving}

    assert {h: r.status_code for h, r in answers.items()} == dict.fromkeys(not_serving, 404)
    assert {r.text for r in answers.values()} == {'{"detail":"Not found."}'}


def test_an_answer_is_kept_for_a_minute(client: TestClient, rows: _Rows, clock: _Clock) -> None:
    practice_id, slug = rows.practice()
    host = rows.host(practice_id, primary=True, status="pending")
    assert _ask(client, host).status_code == 404

    rows.set_status(host, "active")
    clock.now += 59
    assert _ask(client, host).status_code == 404, "the kept answer stands until the minute is up"

    clock.now += 1
    assert _ask(client, host).json() == {"slug": slug, "primary_host": host}

    rows.set_status(host, "error")
    assert _ask(client, host).status_code == 200
    clock.now += 60
    assert _ask(client, host).status_code == 404


# ---------------------------------------------------------------------------
# Where a portal link points
# ---------------------------------------------------------------------------


@pytest.fixture
def links(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The shared portal address configured, and no resolver registered."""
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()
    factory.reset_delivery_registrations()
    yield
    factory.reset_delivery_registrations()
    get_settings.cache_clear()


@pytest.mark.usefixtures("links")
def test_a_link_goes_to_the_root_of_the_working_primary_host(rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, primary=True)
    rows.host(practice_id, prefix="clients")

    assert factory.build_portal_link(slug=slug) == f"https://{primary}/"
    assert factory.build_invite_link(slug=slug, token=_STAND_IN_INVITE) == (
        f"https://{primary}/#invite=abc.def.ghi"
    )


@pytest.mark.usefixtures("links")
def test_a_link_falls_back_while_the_primary_is_not_working(rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, primary=True, status="error")
    fallback = f"https://app.example.test/portal/{slug}"

    assert factory.portal_page_url(slug) == fallback

    rows.set_status(primary, "active")
    assert factory.portal_page_url(slug) == f"https://{primary}/", "links are never cached"


@pytest.mark.usefixtures("links")
def test_a_link_falls_back_with_no_primary_host(rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    rows.host(practice_id, prefix="clients")

    assert factory.portal_page_url(slug) == f"https://app.example.test/portal/{slug}"


@pytest.mark.usefixtures("links")
def test_a_registered_resolver_still_decides_and_can_ask_the_same_question(rows: _Rows) -> None:
    practice_id, slug = rows.practice()
    primary = rows.host(practice_id, primary=True)
    _, plain_slug = rows.practice()

    factory.register_portal_address_resolver(lambda s: f"https://clients.example.test/{s}")
    assert factory.portal_page_url(slug) == f"https://clients.example.test/{slug}"

    def resolver(s: str) -> str:
        host = primary_portal_host_for_slug(s)
        return portal_host_root_url(host) if host else f"https://clients.example.test/{s}"

    factory.register_portal_address_resolver(resolver)
    assert factory.portal_page_url(slug) == f"https://{primary}/"
    assert factory.portal_page_url(plain_slug) == f"https://clients.example.test/{plain_slug}"


def test_an_unknown_slug_has_no_primary_host() -> None:
    assert primary_portal_host_for_slug(f"nobody-{uuid.uuid4().hex[:10]}") is None
