# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The three hottest read routes hand their pooled connection back before serializing.

``GET /api/patients``, ``GET /api/users/me/status`` and ``GET
/api/patients/{id}/notes`` (the first notes read a chart open issues) call
``release_db_connection()`` after their last query. These tests drive each
route through the real middleware and dependencies into a provisioned
practice and watch the MAIN engine's pool (``get_engine().pool``, the request
session's bind) for the whole request, teardown included:

* the route released exactly once, and the release was not a no-op: the
  request session had an open transaction when it was called, so the sync
  route really can see the session from the threadpool;
* the pool had nothing checked out right after the release;
* no main-pool checkout happened after the release. A lazy load of an expired
  ORM attribute, a query moved below the release, or a teardown that queries
  would each show up here as a second checkout.

The standalone lookups (practice resolution and the like) use the nested
engine and are not counted. Two more checks per route: the body is the same
as when the release is a no-op (the behaviour before this change), and a
second clinician in the same practice still sees only their own rows.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.pool import QueuePool

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "hot-reads-release@example.com"
_CLINICIAN_A = "3f1f6a0e-7c59-4b55-9d0e-1a2b3c4d5e6f"
_CLINICIAN_B = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"

# Every name the three routes reach the helper through: two module-level
# imports, and the function-local ``from ..db import`` in users.py.
_RELEASE_TARGETS = (
    "app.routes.patients.release_db_connection",
    "app.routes.notes.release_db_connection",
    "app.db.release_db_connection",
)


@dataclass
class _Release:
    """What the pool looked like around one call of the real helper."""

    held_before: bool
    checked_out_after: int
    events_before: int


@dataclass
class _PoolWatch:
    events: list[str] = field(default_factory=list)
    releases: list[_Release] = field(default_factory=list)

    def checkouts_after_release(self) -> list[str]:
        assert len(self.releases) == 1, f"expected one release, saw {len(self.releases)}"
        later = self.events[self.releases[0].events_before :]
        return [e for e in later if e == "checkout"]


@contextmanager
def _watching_main_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[_PoolWatch]:
    """Record main-pool checkouts/checkins and each real release, for the block."""
    from app import db  # noqa: PLC0415

    real_release = db.release_db_connection
    pool = db.get_engine().pool
    assert isinstance(pool, QueuePool), f"expected the request engine's QueuePool, got {pool!r}"
    watch = _PoolWatch()

    def on_checkout(*_args: Any) -> None:
        watch.events.append("checkout")

    def on_checkin(*_args: Any) -> None:
        watch.events.append("checkin")

    def spy() -> None:
        held = any(s.in_transaction() for s in db.bound_db_sessions())
        events_before = len(watch.events)
        real_release()
        watch.releases.append(
            _Release(
                held_before=held,
                checked_out_after=pool.checkedout(),
                events_before=events_before,
            )
        )

    for target in _RELEASE_TARGETS:
        monkeypatch.setattr(target, spy)
    event.listen(pool, "checkout", on_checkout)
    event.listen(pool, "checkin", on_checkin)
    try:
        yield watch
    finally:
        event.remove(pool, "checkout", on_checkout)
        event.remove(pool, "checkin", on_checkin)


def _release_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the routes back to holding the connection until teardown."""
    for target in _RELEASE_TARGETS:
        monkeypatch.setattr(target, lambda: None)


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def client_as(
    fastapi_app: FastAPI,
    tenant_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], TestClient]]:
    """A factory for a client signed in as one clinician of the practice.

    The middleware resolves the practice for real from the seeded email; the
    auth dependencies return the clinician, and the tenant context arms the
    RLS principal the way the production dependency does, so a second
    clinician is held to their own rows by the database, not by the route.
    """
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    def _stash_identity(request: Request) -> None:
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=_CLINICIAN_A,
            email=_TENANT_EMAIL,
            email_verified=True,
            mfa_satisfied=True,
            claims={},
        )

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _make(user_id: str) -> TestClient:
        user = User(
            id=user_id,
            email=_TENANT_EMAIL,
            name=f"Clinician {user_id[:4]}",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_version="2024-01-01",
        )

        def _tenant_context() -> TenantContext:
            arm_current_user_id(get_db_session(), user_id)
            return TenantContext(
                user_id=user_id, practice_id="test-tenant", practice_schema=tenant_schema
            )

        def _armed_user() -> User:
            # As ``_resolve_user`` does behind every real user dependency;
            # /me/status depends on nothing else that would arm it.
            arm_current_user_id(get_db_session(), user_id)
            return user

        overrides = fastapi_app.dependency_overrides
        overrides[get_current_user_id] = lambda: user_id
        overrides[get_current_user] = _armed_user
        overrides[get_current_user_no_mfa] = _armed_user
        overrides[require_active_subscription] = _armed_user
        overrides[require_baa_acceptance] = _armed_user
        overrides[get_tenant_context] = _tenant_context
        return TestClient(fastapi_app)

    try:
        yield _make
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
def seeded(engine: Engine, tenant_schema: str, client_as: Callable[[str], TestClient]) -> str:
    """Clinician A has one client with two notes, one deleted client and a profile.

    One note is standalone and one belongs to an imported session. B has nothing.

    Returns A's patient id.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        conn.execute(
            text(
                "TRUNCATE TABLE notes, patient_clinicians, patients, audit_logs, "
                "clinician_profiles RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": _CLINICIAN_A}
        )
        conn.execute(
            text(
                "INSERT INTO clinician_profiles (user_id, practice_id, title, role, joined_at) "
                "VALUES (CAST(:uid AS uuid), 'test-tenant', 'LCSW', 'clinician', now())"
            ),
            {"uid": _CLINICIAN_A},
        )
        conn.commit()

    client = client_as(_CLINICIAN_A)
    created = client.post("/api/patients", json={"first_name": "Ada", "last_name": "Lovelace"})
    assert created.status_code == 201, created.text
    patient_id: str = created.json()["id"]
    note = client.post(f"/api/patients/{patient_id}/notes", json={"note_type": "soap"})
    assert note.status_code == 201, note.text
    # One session-bound note too: only those send imported_note_ids to the
    # database, and that lookup is the notes route's last query.
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": _CLINICIAN_A}
        )
        session_id = conn.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                "session_number, status, transcript, created_at) VALUES (gen_random_uuid(), "
                "CAST(:uid AS uuid), CAST(:pid AS uuid), now(), 1, 'imported', "
                "CAST(:transcript AS jsonb), now()) RETURNING id"
            ),
            {
                "uid": _CLINICIAN_A,
                "pid": patient_id,
                "transcript": '{"format": "txt", "content": "Imported."}',
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO notes (id, patient_id, session_id, note_type, created_at, "
                "updated_at) VALUES (gen_random_uuid(), CAST(:pid AS uuid), :sid, 'soap', "
                "now() - interval '1 day', now())"
            ),
            {"pid": patient_id, "sid": session_id},
        )
        conn.commit()
    # One soft-deleted client too, so the recently-deleted branch audits a row.
    gone = client.post("/api/patients", json={"first_name": "Gone", "last_name": "Away"})
    assert gone.status_code == 201, gone.text
    deleted = client.request(
        "DELETE",
        f"/api/patients/{gone.json()['id']}",
        json={"acknowledged_retention_obligation": True},
    )
    assert deleted.status_code in (200, 204), deleted.text
    return patient_id


def _paths(patient_id: str) -> dict[str, str]:
    return {
        "patients": "/api/patients",
        "recently_deleted": "/api/patients?include_deleted=recent",
        "status": "/api/users/me/status",
        "notes": f"/api/patients/{patient_id}/notes",
    }


ROUTES = ("patients", "recently_deleted", "status", "notes")


@pytest.mark.parametrize("route", ROUTES)
def test_route_returns_its_connection_before_serializing(
    route: str,
    seeded: str,
    client_as: Callable[[str], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = client_as(_CLINICIAN_A)
    with _watching_main_pool(monkeypatch) as watch:
        response = client.get(_paths(seeded)[route])

    assert response.status_code == 200, response.text
    assert len(watch.releases) == 1, "the route must release its connection exactly once"
    release = watch.releases[0]
    assert release.held_before, (
        "release_db_connection() found no open transaction: in the threadpool it "
        "was a no-op, so nothing went back to the pool"
    )
    assert release.checked_out_after == 0, "a main-pool connection was still out after release"
    assert watch.checkouts_after_release() == [], (
        "a second main-pool checkout after the release: something queried (or "
        "lazy-loaded an expired ORM attribute) after the route let go"
    )


@pytest.mark.parametrize("route", ROUTES)
def test_route_body_is_unchanged_by_the_release(
    route: str,
    seeded: str,
    client_as: Callable[[str], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = client_as(_CLINICIAN_A)
    path = _paths(seeded)[route]
    released = client.get(path)

    with monkeypatch.context() as held:
        _release_is_a_no_op(held)
        unreleased = client.get(path)

    assert released.status_code == unreleased.status_code == 200, released.text
    assert released.json() == unreleased.json()


def test_patients_list_body_has_the_clients_rows(
    seeded: str, client_as: Callable[[str], TestClient]
) -> None:
    """The equality above is not two empty lists agreeing."""
    body = client_as(_CLINICIAN_A).get("/api/patients").json()
    assert [(p["id"], p["first_name"]) for p in body["data"]] == [(seeded, "Ada")]
    assert body["total"] == 1


def test_recently_deleted_body_has_the_deleted_client(
    seeded: str, client_as: Callable[[str], TestClient]
) -> None:
    body = client_as(_CLINICIAN_A).get("/api/patients?include_deleted=recent").json()
    assert [p["first_name"] for p in body["data"]] == ["Gone"]


def test_notes_body_has_both_notes(seeded: str, client_as: Callable[[str], TestClient]) -> None:
    body = client_as(_CLINICIAN_A).get(f"/api/patients/{seeded}/notes").json()
    assert body["total"] == 2
    assert {n["patient_id"] for n in body["data"]} == {seeded}
    # The session-bound one reads as imported: imported_note_ids ran.
    assert sorted(bool(n["imported"]) for n in body["data"]) == [False, True]


def test_status_body_reads_the_callers_profile(
    seeded: str, client_as: Callable[[str], TestClient]
) -> None:
    assert client_as(_CLINICIAN_A).get("/api/users/me/status").json()["title"] == "LCSW"


def test_second_clinician_sees_no_other_clinicians_clients(
    seeded: str, client_as: Callable[[str], TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_as(_CLINICIAN_B)
    with _watching_main_pool(monkeypatch) as watch:
        body = client.get("/api/patients").json()
    assert body["data"] == []
    assert body["total"] == 0
    assert len(watch.releases) == 1
    assert watch.releases[0].held_before


def test_second_clinician_cannot_read_another_clinicians_notes(
    seeded: str, client_as: Callable[[str], TestClient]
) -> None:
    response = client_as(_CLINICIAN_B).get(f"/api/patients/{seeded}/notes")
    assert response.status_code == 404, response.text


def test_second_clinician_status_carries_their_own_profile(
    seeded: str, client_as: Callable[[str], TestClient]
) -> None:
    body = client_as(_CLINICIAN_B).get("/api/users/me/status").json()
    assert body["title"] is None
    assert body["name"] == f"Clinician {_CLINICIAN_B[:4]}"


def test_watch_sees_a_checkout_after_a_late_query(
    seeded: str, client_as: Callable[[str], TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counter can fail: a query after the release shows up as a checkout."""
    from app import db  # noqa: PLC0415

    client = client_as(_CLINICIAN_A)
    with _watching_main_pool(monkeypatch) as watch:
        spy = db.release_db_connection

        def release_then_query() -> None:
            spy()
            db.get_db_session().execute(text("SELECT 1"))

        monkeypatch.setattr("app.routes.patients.release_db_connection", release_then_query)
        response = client.get("/api/patients")

    assert response.status_code == 200, response.text
    assert watch.checkouts_after_release() == ["checkout"]
