# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A covering grant that expires mid-session stops working on the next request.

One practice, two clinicians. Avery holds the ``primary`` grant on Xavier and
owns his chart: a draft note, a document, a portal thread with a message from
him. Avery goes away and names Blake to cover; Blake accepts through
``/api/covering``, so the ``covering`` grant on Xavier is the one the shipped
code writes, with the away dates as its window. Blake also holds a grant on a
client of their own, Yolanda, so Blake is a working clinician throughout and
a 404 below is never a broken account.

Every case is one route, run on the same app and the same test client from
start to finish:

1. **Inside the window**, Blake calls the route on Xavier's object and gets a
   2xx. For a write this is a real write, which is what the editor or reply
   box Blake has open would send.
2. **The grant expires**, one of two ways: the away period's return date
   passes (``expires_at`` moves into the past, as midnight on the return day
   does), or Avery ends the period early through
   ``POST /api/away-periods/{id}/end``.
3. **Blake's next request** on the same object is a 404, never 403 or 409.
   After every write attempt the object is exactly as it was: same content
   and ``updated_at``, no new message, no tombstone, and no audit row from
   Blake. Avery still reaches the object, and Blake still reaches Yolanda.

The connection is subject to row security and the request's principal is
armed as whoever is calling, so a denial that leans on ``has_patient_access``
is tested as row security, and one that leans on the application's own grant
check is tested as that.

The document delete case uses a chart document Blake uploaded on Xavier
while covering: deleting is the uploader's, so a document Avery uploaded was
never Blake's to delete, expired grant or not.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_AVERY = "a7e2c0de-0000-4000-8000-0000000000a1"
_BLAKE = "b1a5e0de-0000-4000-8000-0000000000b2"
_CLINICIANS = {
    _AVERY: ("covering-expiry-away@example.com", "Avery Away"),
    _BLAKE: ("covering-expiry-cover@example.com", "Blake Cover"),
}
_REVIEWED = "Thanks for writing. I'm away for a few days."


@dataclass
class _Clients:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class _Chart:
    """Xavier's objects for one test, written fresh so no test sees another's edits."""

    note: str = field(default_factory=lambda: str(uuid.uuid4()))
    doc: str = field(default_factory=lambda: str(uuid.uuid4()))
    blakes_doc: str = field(default_factory=lambda: str(uuid.uuid4()))
    blakes_other_doc: str = field(default_factory=lambda: str(uuid.uuid4()))
    thread: str = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Module-scoped infra
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars as this module found them."""
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    tokens = [
        (var, var.set(var.get()))
        for var in (_current_user_id, _current_patient_id, _current_tenant_schema)
    ]
    yield
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice with both clinicians mapped to it as members."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    # The row policies call ``has_patient_access`` unqualified; see
    # ``test_patients_api_e2e.tenant_schema`` for why the pool is warmed.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    practice_id = str(uuid.uuid4())
    schema = f"practice_{practice_id.replace('-', '_')}"
    now = datetime.now(UTC)
    create_practice_schema(engine, schema)
    with OrmSession(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Covering expiry",
                schema_name=schema,
                owner_email=_CLINICIANS[_AVERY][0],
                owner_user_id=_AVERY,
                created_at=now,
            )
        )
        for user_id, (email, name) in _CLINICIANS.items():
            session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=now))
            session.add(
                EmailTenantMappingRow(
                    email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
                )
            )
        session.commit()
    yield schema
    with OrmSession(engine) as session:
        for user_id, (email, _name) in _CLINICIANS.items():
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(PlatformUserRow).filter_by(id=user_id).delete()
        session.query(PracticeRow).filter_by(id=practice_id).delete()
        session.commit()
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _scoped(conn: Connection, schema: str, user_id: str) -> None:
    """Scope *conn* to the practice and arm it as *user_id*, as a request would."""
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


@pytest.fixture(scope="module")
def clients(engine: Engine, tenant_schema: str) -> _Clients:
    """Xavier is Avery's client, Yolanda is Blake's; each written as their clinician."""
    c = _Clients()
    for patient, first, owner in ((c.xavier, "Xavier", _AVERY), (c.yolanda, "Yolanda", _BLAKE)):
        with engine.begin() as conn:
            _scoped(conn, tenant_schema, owner)
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, 'Client', lower(:first), 'client', "
                    "'active', 0, now(), now())"
                ),
                {"pid": patient, "first": first},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by, role) "
                    "VALUES (CAST(:pid AS uuid), :u, :u, 'primary')"
                ),
                {"pid": patient, "u": owner},
            )
    return c


@pytest.fixture
def chart(engine: Engine, tenant_schema: str, clients: _Clients) -> Iterator[_Chart]:
    """Xavier's draft note, documents and thread, written as Avery; no coverage after."""
    c = _Chart()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    with engine.begin() as conn:
        _scoped(conn, tenant_schema, _AVERY)
        conn.execute(
            text(
                "INSERT INTO notes (id, patient_id, session_id, note_type, content, "
                "status, author_user_id, restricted, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, 'soap', "
                "CAST(:c AS jsonb), 'draft', CAST(:u AS uuid), false, :at, :at)"
            ),
            {
                "id": c.note,
                "pid": clients.xavier,
                "u": _AVERY,
                "c": json.dumps(
                    {"subjective": "Avery's draft", "objective": "", "assessment": "", "plan": ""}
                ),
                "at": at,
            },
        )
        # Chart documents follow the patient grant. Blake's two stand for files
        # Blake uploaded on Xavier while covering.
        for doc_id, uploader, name in (
            (c.doc, _AVERY, "referral.pdf"),
            (c.blakes_doc, _BLAKE, "covering-upload.pdf"),
            (c.blakes_other_doc, _BLAKE, "covering-upload-2.pdf"),
        ):
            conn.execute(
                text(
                    "INSERT INTO patient_documents (id, patient_id, user_id, filename, "
                    "mime_type, gcs_path, extracted_text, size_bytes, category, "
                    "created_at, finalized_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:u AS uuid), "
                    ":name, 'application/pdf', :gcs, 'text', 1, 'chart', :at, :at)"
                ),
                {
                    "id": doc_id,
                    "pid": clients.xavier,
                    "u": uploader,
                    "name": name,
                    "gcs": f"patients/{clients.xavier}/{name}",
                    "at": at,
                },
            )
        conn.execute(
            text(
                "INSERT INTO patient_message_threads (id, patient_id, subject, status, "
                "created_at, last_message_at) VALUES (CAST(:t AS uuid), CAST(:p AS uuid), "
                "'Rescheduling', 'open', :at, :at)"
            ),
            {"t": c.thread, "p": clients.xavier, "at": at},
        )
        conn.execute(
            text(
                "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, "
                "created_at) VALUES (CAST(:m AS uuid), CAST(:t AS uuid), CAST(:p AS uuid), "
                "'patient', 'Can we move Thursday?', :at)"
            ),
            {"m": str(uuid.uuid4()), "t": c.thread, "p": clients.xavier, "at": at},
        )
    yield c
    with engine.begin() as conn:
        _scoped(conn, tenant_schema, _BLAKE)
        conn.execute(text("DELETE FROM patient_clinicians WHERE role = 'covering'"))
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("DELETE FROM clinician_away_periods"))


@pytest.fixture
def acting(
    fastapi_app: FastAPI, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    """The real route stack, signed in as whichever clinician ``acting["id"]`` names."""
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

    who = {"id": _AVERY}

    def _user() -> User:
        email, name = _CLINICIANS[who["id"]]
        return User(
            id=who["id"],
            email=email,
            name=name,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_version="2024-01-01",
        )

    def _stash_identity(request: Request) -> None:
        email, _name = _CLINICIANS[who["id"]]
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=who["id"],
            email=email,
            email_verified=True,
            mfa_satisfied=True,
            claims={},
        )

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _armed_user() -> User:
        # Arm the CALLER, not a fixed user: a row policy evaluated as the
        # wrong clinician would pass or fail for the wrong reason.
        arm_current_user_id(get_db_session(), who["id"])
        return _user()

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), who["id"])
        return TenantContext(
            user_id=who["id"], practice_id="test-tenant", practice_schema=tenant_schema
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: who["id"]
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    try:
        yield who
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client(fastapi_app: FastAPI, acting: dict[str, str]) -> TestClient:
    """One client for the whole case: nothing about access may ride on it."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    return TestClient(fastapi_app)


# ---------------------------------------------------------------------------
# Coverage, and its two ways to end
# ---------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(UTC).date()


def _cover_now(client: TestClient, acting: dict[str, str]) -> str:
    """Avery is away from yesterday; Blake accepts. Returns the period id."""
    acting["id"] = _AVERY
    created = client.post(
        "/api/away-periods",
        json={
            # Yesterday: a first day away already begun on Avery's calendar,
            # whatever their zone.
            "start_date": (_today() - timedelta(days=1)).isoformat(),
            "end_date": (_today() + timedelta(days=5)).isoformat(),
            "covering_user_id": _BLAKE,
            "acknowledgment_text": _REVIEWED,
        },
    )
    assert created.status_code == 201, created.text
    period_id = str(created.json()["id"])
    acting["id"] = _BLAKE
    accepted = client.post(f"/api/covering/{period_id}/accept")
    assert accepted.status_code == 200, accepted.text
    return period_id


def _return_date_passes(
    engine: Engine, schema: str, client: TestClient, acting: dict[str, str], period_id: str
) -> None:
    """Midnight on the return day has gone by: ``expires_at`` is behind us."""
    with engine.begin() as conn:
        _scoped(conn, schema, _BLAKE)
        moved = conn.execute(
            text(
                "UPDATE patient_clinicians SET expires_at = now() - interval '1 second' "
                "WHERE role = 'covering' AND away_period_id = CAST(:p AS uuid)"
            ),
            {"p": period_id},
        ).rowcount
    assert moved == 1


def _ended_early(
    engine: Engine, schema: str, client: TestClient, acting: dict[str, str], period_id: str
) -> None:
    """Avery comes back early and ends the period through the route."""
    acting["id"] = _AVERY
    ended = client.post(f"/api/away-periods/{period_id}/end")
    assert ended.status_code == 200, ended.text


_EXPIRIES: dict[str, Callable[[Engine, str, TestClient, dict[str, str], str], None]] = {
    "return-date-passed": _return_date_passes,
    "ended-early": _ended_early,
}


# ---------------------------------------------------------------------------
# The routes: what Blake sends inside the window, and again after it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Route:
    """One request Blake makes on Xavier's chart.

    ``control`` is the same request inside the window, on an object of the
    same kind; ``None`` means the request itself. ``reset`` runs as Avery
    after the control, so a write that the control already made cannot hide
    a second one (closing a closed thread changes nothing).
    """

    method: str
    path: Callable[[_Chart, _Clients], str]
    body: dict[str, Any] | None = None
    control_path: Callable[[_Chart, _Clients], str] | None = None
    reset: Callable[[TestClient, _Chart], None] | None = None


def _reopen_as_avery(client: TestClient, c: _Chart) -> None:
    assert client.post(f"/api/message-threads/{c.thread}/reopen").status_code == 200


_ROUTES: dict[str, _Route] = {
    "note-read": _Route("GET", lambda c, _p: f"/api/notes/{c.note}"),
    "note-edit": _Route(
        "PATCH",
        lambda c, _p: f"/api/notes/{c.note}",
        body={
            "content_edited": {
                "subjective": "Blake's covering edit",
                "objective": "",
                "assessment": "",
                "plan": "",
            }
        },
    ),
    "document-read": _Route("GET", lambda c, _p: f"/api/documents/{c.doc}"),
    "document-list": _Route("GET", lambda _c, p: f"/api/patients/{p.xavier}/documents"),
    "document-delete": _Route(
        "DELETE",
        lambda c, _p: f"/api/documents/{c.blakes_doc}",
        control_path=lambda c, _p: f"/api/documents/{c.blakes_other_doc}",
    ),
    "thread-read": _Route("GET", lambda c, _p: f"/api/message-threads/{c.thread}"),
    "thread-list": _Route("GET", lambda _c, p: f"/api/patients/{p.xavier}/message-threads"),
    "thread-reply": _Route(
        "POST",
        lambda c, _p: f"/api/message-threads/{c.thread}/replies",
        body={"body": "Thursday at 3 works. Blake, covering for Avery."},
    ),
    "thread-close": _Route(
        "POST",
        lambda c, _p: f"/api/message-threads/{c.thread}/close",
        reset=_reopen_as_avery,
    ),
    "thread-mark-read": _Route("POST", lambda c, _p: f"/api/message-threads/{c.thread}/read"),
}

#: How Avery reads each kind of object afterwards: it is still there.
_AVERYS_READ: dict[str, Callable[[_Chart, _Clients], str]] = {
    "note": lambda c, _p: f"/api/notes/{c.note}",
    "document": lambda c, _p: f"/api/documents/{c.doc}",
    "thread": lambda c, _p: f"/api/message-threads/{c.thread}",
}


def _send(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> Response:
    return client.request(method, path, json=body)


def _footprint(engine: Engine, schema: str, clients: _Clients) -> dict[str, Any]:
    """Everything a write by Blake could have touched on Xavier's chart.

    Read as Avery, who holds the primary grant, so a row Blake changed is
    visible here. Blake's read markers are read as Blake, their only reader.
    Blake's audit rows are read under the purge setting, the
    one that sees every actor's rows (``audit_logs`` is FORCE RLS).
    """
    with engine.begin() as conn:
        _scoped(conn, schema, _AVERY)
        p = {"p": clients.xavier}
        notes = conn.execute(
            text(
                "SELECT id::text, content::text, content_edited::text, status, updated_at, "
                "deleted_at FROM notes WHERE patient_id = CAST(:p AS uuid) ORDER BY id"
            ),
            p,
        ).all()
        documents = conn.execute(
            text(
                "SELECT id::text, deleted_at FROM patient_documents "
                "WHERE patient_id = CAST(:p AS uuid) ORDER BY id"
            ),
            p,
        ).all()
        threads = conn.execute(
            text(
                "SELECT id::text, status, closed_at, closed_by::text, assigned_user_id::text, "
                "clinician_last_read_at, last_message_at FROM patient_message_threads "
                "WHERE patient_id = CAST(:p AS uuid) ORDER BY id"
            ),
            p,
        ).all()
        messages = conn.execute(
            text(
                "SELECT id::text, thread_id::text, sender, body, read_at FROM patient_messages "
                "WHERE patient_id = CAST(:p AS uuid) ORDER BY id"
            ),
            p,
        ).all()
    with engine.begin() as conn:
        # A read marker is visible only to its owner, so Blake's is read as Blake.
        _scoped(conn, schema, _BLAKE)
        blakes_reads = conn.execute(
            text(
                "SELECT thread_id::text, last_read_at FROM patient_message_thread_reads "
                "ORDER BY thread_id"
            )
        ).all()
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        blakes_audit = conn.execute(
            text(
                "SELECT id::text, action, resource_id FROM audit_logs "
                "WHERE user_id = :u ORDER BY id"
            ),
            {"u": _BLAKE},
        ).all()
    return {
        "notes": [tuple(r) for r in notes],
        "documents": [tuple(r) for r in documents],
        "threads": [tuple(r) for r in threads],
        "messages": [tuple(r) for r in messages],
        "blakes_reads": [tuple(r) for r in blakes_reads],
        "blakes_audit": [tuple(r) for r in blakes_audit],
    }


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expiry", list(_EXPIRIES))
@pytest.mark.parametrize("route_name", list(_ROUTES))
def test_blakes_next_request_after_the_grant_expires_is_a_404(  # noqa: PLR0913 — fixtures
    route_name: str,
    expiry: str,
    client: TestClient,
    acting: dict[str, str],
    engine: Engine,
    tenant_schema: str,
    clients: _Clients,
    chart: _Chart,
) -> None:
    route = _ROUTES[route_name]
    path = route.path(chart, clients)
    period_id = _cover_now(client, acting)

    # Inside the window: Blake reaches the object, and a write lands.
    acting["id"] = _BLAKE
    control = route.control_path(chart, clients) if route.control_path else path
    inside = _send(client, route.method, control, route.body)
    assert 200 <= inside.status_code < 300, (route_name, inside.status_code, inside.text)
    if route.reset is not None:
        acting["id"] = _AVERY
        route.reset(client, chart)

    _EXPIRIES[expiry](engine, tenant_schema, client, acting, period_id)
    before = _footprint(engine, tenant_schema, clients)

    # The next request, same client, same object: as if it never existed.
    acting["id"] = _BLAKE
    after = _send(client, route.method, path, route.body)
    assert after.status_code == 404, (route_name, after.status_code, after.text)
    assert _footprint(engine, tenant_schema, clients) == before

    # Blake is still a working clinician: their own client is right there.
    assert client.get(f"/api/patients/{clients.yolanda}").status_code == 200
    # And the object is still there for the clinician whose client it is.
    acting["id"] = _AVERY
    kind = route_name.split("-", 1)[0]
    assert client.get(_AVERYS_READ[kind](chart, clients)).status_code == 200
