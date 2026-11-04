# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's booking pages on its own host, against real PostgreSQL.

* ``GET /api/portal/hosts/{host}/booking-links/{slug}`` answers only for an
  active link of the practice whose portal the host serves; another practice's
  link, a link that is off or deleted, and a host that serves no portal are all
  the same 404.
* A booking email's links go to the practice's working primary portal host, and
  to the app's address otherwise.
* The practice's "Book a session" link goes to the booking link it chose, on the
  same host, or to its outside address — and nowhere once it is off, or the
  booking link is off, deleted or not the practice's.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from app.portal import host_routes
from app.portal.booking_button import booking_button_url
from app.portal.booking_hosts import BookingHostCache, booking_page_origin, get_booking_host_cache
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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

URL = "/api/portal/hosts/{host}/booking-links/{slug}"


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
    users: list[str] = field(default_factory=list)

    def practice(self, *, portal: bool = True) -> str:
        """A practice with a portal address and the portal on or off."""
        practice_id = f"booking-hosts-{uuid.uuid4().hex[:10]}"
        user_id = str(uuid.uuid4())
        self.practices.append(practice_id)
        self.users.append(user_id)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practices"
                    " (id, name, schema_name, owner_email, product, status, is_active, created_at)"
                    " VALUES (:id, 'Example Therapy', :schema, 'owner@example.com',"
                    "         'pablo', 'active', TRUE, now())"
                ),
                {"id": practice_id, "schema": f"schema_{practice_id.replace('-', '_')}"},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.users (id, email, name, created_at, status,"
                    " is_platform_admin, chat_quality_review_opt_in,"
                    " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                    " VALUES (CAST(:u AS uuid), :email, 'Owner', now(), 'approved', false,"
                    " false, false, false)"
                ),
                {"u": user_id, "email": f"{user_id}@example.test"},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.companion_practice_slugs "
                    "(slug, practice_id, display_name, created_at) "
                    "VALUES (:s, :p, 'Example Therapy', now())"
                ),
                {"s": f"hosts-{uuid.uuid4().hex[:10]}", "p": practice_id},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.practice_portal_settings "
                    "(practice_id, enabled, decided_at, updated_at) "
                    "VALUES (:p, :e, now(), now())"
                ),
                {"p": practice_id, "e": portal},
            )
        return practice_id

    def host(self, practice_id: str, *, status: str = "active", primary: bool = True) -> str:
        host = f"portal.{uuid.uuid4().hex[:10]}.example.com"
        self.hosts.append(host)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domains "
                    "(domain, practice_id, purpose, kind, status, is_primary, created_at) "
                    "VALUES (:d, :p, 'portal', 'vanity', :status, :primary, now())"
                ),
                {"d": host, "p": practice_id, "status": status, "primary": primary},
            )
        return host

    def link(self, practice_id: str, *, active: bool = True, deleted: bool = False) -> str:
        slug = f"intro-{uuid.uuid4().hex[:10]}"
        owner = self.users[self.practices.index(practice_id)]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.booking_links (id, slug, user_id, practice_id,"
                    " host_name, title, appointment_type_id, is_active, created_at, updated_at,"
                    " deleted_at)"
                    " VALUES (CAST(:id AS uuid), :slug, CAST(:u AS uuid), :p, 'Sam Rivera',"
                    " 'Intro call', CAST(:t AS uuid), :active, now(), now(),"
                    " CASE WHEN :deleted THEN now() END)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "u": owner,
                    "p": practice_id,
                    "t": str(uuid.uuid4()),
                    "active": active,
                    "deleted": deleted,
                },
            )
        return slug

    def set_active(self, slug: str, active: bool) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE platform.booking_links SET is_active = :a WHERE slug = :s"),
                {"a": active, "s": slug},
            )

    def button(
        self,
        practice_id: str,
        *,
        link: str | None = None,
        outside: str | None = None,
        website: bool = False,
        portal: bool = False,
    ) -> None:
        """The practice's "Book a session" link; *link* is a booking link's slug."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_booking_buttons (practice_id, booking_link_id,"
                    " outside_url, on_website, in_portal, updated_at, updated_by)"
                    " VALUES (:p, (SELECT id FROM platform.booking_links WHERE slug = :l),"
                    " :o, :w, :i, now(), 'owner')"
                    " ON CONFLICT (practice_id) DO UPDATE SET booking_link_id ="
                    " EXCLUDED.booking_link_id, outside_url = EXCLUDED.outside_url,"
                    " on_website = EXCLUDED.on_website, in_portal = EXCLUDED.in_portal"
                ),
                {"p": practice_id, "l": link, "o": outside, "w": website, "i": portal},
            )

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
            for table in (
                "practice_booking_buttons",
                "booking_links",
                "companion_practice_slugs",
                "practice_portal_settings",
                "practices",
            ):
                column = "id" if table == "practices" else "practice_id"
                conn.execute(
                    text(f"DELETE FROM platform.{table} WHERE {column} = ANY(:p)"),  # noqa: S608 — fixed names
                    {"p": self.practices},
                )
            conn.execute(
                text("DELETE FROM platform.users WHERE id = ANY(CAST(:u AS uuid[]))"),
                {"u": self.users},
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
    cache = BookingHostCache(clock=clock)
    app.dependency_overrides[get_booking_host_cache] = lambda: cache
    with TestClient(app) as test_client:
        yield test_client


def _ask(client: TestClient, host: str, slug: str) -> int:
    return client.get(URL.format(host=host, slug=slug)).status_code


def test_a_practices_host_serves_its_own_active_link(client: TestClient, rows: _Rows) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id)
    slug = rows.link(practice_id)

    response = client.get(URL.format(host=f"{host.upper()}:443", slug=slug))

    assert response.status_code == 200
    assert response.json() == {"slug": slug}


def test_every_link_the_host_does_not_serve_is_the_same_404(
    client: TestClient, rows: _Rows
) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id)
    other_id = rows.practice()
    rows.host(other_id)
    portal_off_id = rows.practice(portal=False)

    refused = {
        "another practice's link": (host, rows.link(other_id)),
        "a link that is off": (host, rows.link(practice_id, active=False)),
        "a deleted link": (host, rows.link(practice_id, deleted=True)),
        "a link nobody has": (host, f"nobody-{uuid.uuid4().hex[:8]}"),
        "not a link's shape": (host, "Not_A_Slug"),
        "a host not working yet": (
            rows.host(practice_id, status="pending", primary=False),
            rows.link(practice_id),
        ),
        "a practice with its portal off": (rows.host(portal_off_id), rows.link(portal_off_id)),
        "a host nobody holds": (
            f"unknown.{uuid.uuid4().hex[:8]}.example.com",
            rows.link(practice_id),
        ),
    }

    answers = {
        why: client.get(URL.format(host=host_, slug=slug)) for why, (host_, slug) in refused.items()
    }

    assert {why: r.status_code for why, r in answers.items()} == dict.fromkeys(refused, 404)
    assert {r.text for r in answers.values()} == {'{"detail":"Not found."}'}


def test_an_answer_is_kept_for_a_minute(client: TestClient, rows: _Rows, clock: _Clock) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id)
    slug = rows.link(practice_id)
    assert _ask(client, host, slug) == 200

    rows.set_active(slug, False)
    clock.now += 59
    assert _ask(client, host, slug) == 200, "the kept answer stands until the minute is up"
    clock.now += 1
    assert _ask(client, host, slug) == 404


@pytest.fixture
def app_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("APP_URL", "https://app.example.test")
    get_settings.cache_clear()
    yield "https://app.example.test"
    get_settings.cache_clear()


def test_booking_emails_link_to_the_working_primary_host(rows: _Rows, app_url: str) -> None:
    practice_id = rows.practice()
    rows.host(practice_id, primary=False)
    primary = rows.host(practice_id)

    assert booking_page_origin(practice_id) == f"https://{primary}"

    rows.set_status(primary, "error")
    assert booking_page_origin(practice_id) == app_url, "never cached"


def test_booking_emails_use_the_app_without_a_host_that_serves_them(
    rows: _Rows, app_url: str
) -> None:
    no_host = rows.practice()
    portal_off = rows.practice(portal=False)
    rows.host(portal_off)

    assert booking_page_origin(no_host) == app_url
    assert booking_page_origin(portal_off) == app_url
    assert booking_page_origin(None) == app_url


def _urls(engine: Engine, practice_id: str) -> tuple[str | None, str | None]:
    with Session(engine) as session:
        return (
            booking_button_url(session, practice_id, "website"),
            booking_button_url(session, practice_id, "portal"),
        )


def test_the_book_link_is_off_until_the_practice_turns_it_on(engine: Engine, rows: _Rows) -> None:
    practice_id = rows.practice()
    slug = rows.link(practice_id)
    assert _urls(engine, practice_id) == (None, None)

    rows.button(practice_id, link=slug)
    assert _urls(engine, practice_id) == (None, None)


def test_the_book_link_goes_to_the_chosen_link_on_the_practices_own_host(
    engine: Engine, rows: _Rows, app_url: str
) -> None:
    practice_id = rows.practice()
    slug = rows.link(practice_id)
    rows.button(practice_id, link=slug, website=True)
    assert _urls(engine, practice_id) == (f"{app_url}/book/{slug}", None)

    host = rows.host(practice_id)
    rows.button(practice_id, link=slug, website=True, portal=True)
    assert _urls(engine, practice_id) == (f"https://{host}/book/{slug}",) * 2


def test_the_book_link_goes_to_an_outside_address_as_given(engine: Engine, rows: _Rows) -> None:
    practice_id = rows.practice()
    rows.button(practice_id, outside="https://book.example.net/riverside", portal=True)
    assert _urls(engine, practice_id) == (None, "https://book.example.net/riverside")


def test_a_booking_link_turned_off_or_deleted_takes_the_book_link_away_everywhere(
    engine: Engine, rows: _Rows
) -> None:
    practice_id = rows.practice()
    rows.host(practice_id)
    slug = rows.link(practice_id)
    rows.button(practice_id, link=slug, website=True, portal=True)
    assert None not in _urls(engine, practice_id)

    rows.set_active(slug, False)
    assert _urls(engine, practice_id) == (None, None)

    deleted = rows.link(practice_id, deleted=True)
    rows.button(practice_id, link=deleted, website=True, portal=True)
    assert _urls(engine, practice_id) == (None, None)


def test_another_practices_booking_link_is_never_linked(engine: Engine, rows: _Rows) -> None:
    practice_id = rows.practice()
    other_id = rows.practice()
    rows.button(practice_id, link=rows.link(other_id), website=True, portal=True)
    assert _urls(engine, practice_id) == (None, None)
