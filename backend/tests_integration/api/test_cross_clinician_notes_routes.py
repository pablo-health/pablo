# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Notes, one clinician against another clinician's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and wrote every note on his chart. Blake holds a grant on Yolanda,
with a note of her own, and none on Xavier. Every request runs through the
real app, the real repositories and the real ``has_patient_access``, against
a practice schema provisioned the production way, on a connection subject to
row security. The request's principal is armed as whoever is calling.

Each test gets fresh notes on Xavier's chart, written as Avery through the
note service: drafts, and signed notes with an addendum. Every write route
needs a note in a particular state to succeed (a draft to edit, finalize or
sign; a signed note to unlock or add to), so each route has two notes in that
state: a twin for Avery's control and a target for Blake. Blake's attempt is
then made on a note the action would succeed on, if Blake could see it.

Three kinds of case:

* **Routes that take Avery's note or client by id** (read, edit, finalize,
  the signing record, sign, unlock, addendum, and the client's note list and
  new-note route). Avery calls each one first and it works; then Blake calls
  it on Avery's object and gets 404, never 403 or 409, with nothing of
  Xavier's in the body. Afterwards every note row on Xavier's chart, every
  signed version, every addendum and the audit trail are exactly as they
  were before Blake's request.
* **Blake's own client**, so Blake is a working clinician whose requests
  read their own rows and the 404s above are not an unarmed principal's.
* **Lookups by an id that names nothing**, so a 404 for Blake is the same
  404 a stranger gets.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.models import User
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "e2e-cross-clinician-notes@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000f1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000f2"

# Distinctive enough that a substring match in a body is proof. Letters only:
# a sentinel made of digits would turn up by chance in ids and timestamps.
_X_LAST = "Thistlewaitex"
_X_BODY = "Xavier described the harbor dream again"
_X_ADDENDUM = "Xavier called afterwards about the harbor"
_Y_LAST = "Brackenmoory"
_Y_BODY = "Yolanda kept the sleep diary all week"
# What Blake tries to write. If any of it reaches a row, the footprint shows it.
_B_TEXT = "Blake was here"


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_note: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class _Notes:
    """Fresh notes on Xavier's chart, all Avery's, for one test."""

    draft_twin: str
    draft: str
    signed_twin: str
    signed: str


@dataclass
class _Caller:
    """Who the next request is from. Every auth override reads this."""

    user: User


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
    """A practice provisioned the production way, mapped to ``_TENANT_EMAIL``."""
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    # The row policies call ``has_patient_access`` unqualified; see
    # ``test_patients_api_e2e.tenant_schema`` for why the pool is warmed.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _open_session(engine: Engine, schema: str, user_id: str) -> tuple[Session, tuple[Any, ...]]:
    """An ORM session on the practice schema, armed as ``user_id`` and nobody else.

    All three ContextVars are set, the patient principal to ``None``: the
    ``after_begin`` listener reads them, and a fixture runs before any
    per-test reset, so whatever an earlier module left armed must not leak in.
    """
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    tokens = (
        _current_tenant_schema.set(schema),
        _current_user_id.set(user_id),
        _current_patient_id.set(None),
    )
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session, tokens


def _close_session(session: Session, tokens: tuple[Any, ...]) -> None:
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    session.close()
    _current_tenant_schema.reset(tokens[0])
    _current_user_id.reset(tokens[1])
    _current_patient_id.reset(tokens[2])


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client; Blake's with a note of Blake's own."""
    s = _Seed()
    _seed_patient(engine, tenant_schema, _AVERY, s.xavier, "Xavier", _X_LAST)
    _seed_patient(engine, tenant_schema, _BLAKE, s.yolanda, "Yolanda", _Y_LAST)
    session, tokens = _open_session(engine, tenant_schema, _BLAKE)
    try:
        _insert_draft(session, s.y_note, s.yolanda, _BLAKE, _Y_BODY)
        session.commit()
    finally:
        _close_session(session, tokens)
    return s


def _seed_patient(  # noqa: PLR0913 — one row's columns
    engine: Engine, schema: str, clinician: str, patient: str, first: str, last: str
) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, session_count, "
                "created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), :fn, :ln, :fnl, :lnl, 'active', 0, now(), now())"
            ),
            {"pid": patient, "fn": first, "ln": last, "fnl": first.lower(), "lnl": last.lower()},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient, "u": clinician},
        )


def _insert_draft(session: Session, note_id: str, patient_id: str, author: str, body: str) -> None:
    """A hand-written SOAP draft with no recording behind it."""
    session.execute(
        text(
            "INSERT INTO notes (id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, 'soap', "
            "CAST(:c AS jsonb), 'complete', CAST(:author AS uuid), false, now(), now())"
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "c": json.dumps({"subjective": body, "objective": "", "assessment": "", "plan": ""}),
            "author": author,
        },
    )


@pytest.fixture
def notes(engine: Engine, tenant_schema: str, seed: _Seed) -> _Notes:
    """Two drafts and two signed notes (each with an addendum) on Xavier's chart.

    Written as Avery, through the note service and the real repository, so the
    signed versions and the addendum chain are the ones the routes write.
    """
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.services.note_service import NoteService  # noqa: PLC0415

    n = _Notes(*(str(uuid.uuid4()) for _ in range(4)))
    session, tokens = _open_session(engine, tenant_schema, _AVERY)
    try:
        service = NoteService(PostgresNotesRepository(session))
        for note_id in (n.draft_twin, n.draft, n.signed_twin, n.signed):
            _insert_draft(session, note_id, seed.xavier, _AVERY, _X_BODY)
        for note_id in (n.signed_twin, n.signed):
            service.sign_note(
                note_id, signer_name="Avery Ames", signer_credentials="LCSW", user_id=_AVERY
            )
            service.add_addendum(
                note_id,
                text=_X_ADDENDUM,
                signer_name="Avery Ames",
                signer_credentials="LCSW",
                user_id=_AVERY,
            )
        session.commit()
    finally:
        _close_session(session, tokens)
    return n


# ---------------------------------------------------------------------------
# Per-test client: one app, the caller switched per request
# ---------------------------------------------------------------------------


def _user(user_id: str) -> User:
    from app.models import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=_TENANT_EMAIL,
        name="Avery Ames" if user_id == _AVERY else "Blake Byrne",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def caller() -> _Caller:
    return _Caller(user=_user(_AVERY))


@pytest.fixture
def client(  # noqa: PLR0913 — fixture composition mirrors the FastAPI deps
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
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
    from fastapi.testclient import TestClient  # noqa: PLC0415

    del seed
    _truncate_audit(engine, tenant_schema)

    def _stash_identity(request: Request) -> None:
        # The middleware resolves the practice from this identity's email,
        # through the real lookup; the subject is whoever is calling.
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=caller.user.id,
            email=_TENANT_EMAIL,
            email_verified=True,
            mfa_satisfied=True,
            claims={},
        )

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _tenant_context() -> TenantContext:
        # Arm the CALLER, not a fixed user: a row policy evaluated as the
        # wrong clinician would pass or fail for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return TenantContext(
            user_id=caller.user.id,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    def _user_now() -> User:
        # The real user dependency arms the RLS principal at its shared seam
        # (``app.auth.service``). Overriding it must keep the arming, or a
        # request could read nothing for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return caller.user

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: caller.user.id
    fastapi_app.dependency_overrides[get_current_user] = _user_now
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _user_now
    fastapi_app.dependency_overrides[require_active_subscription] = _user_now
    fastapi_app.dependency_overrides[require_baa_acceptance] = _user_now
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()
        _truncate_audit(engine, tenant_schema)


def _truncate_audit(engine: Engine, schema: str) -> None:
    """Empty the audit trail; keep the seeded charts."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        # audit_logs is append-only; a fixture reset is an authorized purge.
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        conn.execute(text("TRUNCATE TABLE audit_logs"))
        conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as(caller: _Caller, user_id: str) -> None:
    caller.user = _user(user_id)


def _rows(conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _footprint(engine: Engine, schema: str, seed: _Seed) -> dict[str, Any]:
    """Every note-side row on Xavier's chart, and the whole audit trail.

    The chart is read as Avery, who holds its grant and so sees every row on
    it whoever wrote them. The audit trail is read under the purge GUC, the
    one setting that sees every actor's rows (``audit_logs`` is FORCE RLS).
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _AVERY})
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        pid = {"pid": seed.xavier}
        footprint = {
            "notes": _rows(
                conn,
                "SELECT id::text, status, content::text AS content, "
                "content_edited::text AS content_edited, finalized_at, updated_at, "
                "quality_rating, deleted_at FROM notes "
                "WHERE patient_id = CAST(:pid AS uuid) ORDER BY id",
                pid,
            ),
            "signatures": _rows(
                conn,
                "SELECT id::text, note_id::text, version, signed_by::text, signed_at, "
                "unlocked_at, unlocked_by::text, unlock_reason FROM note_signatures "
                "WHERE patient_id = CAST(:pid AS uuid) ORDER BY id",
                pid,
            ),
            "addenda": _rows(
                conn,
                "SELECT id::text, note_id::text, text, created_by::text FROM note_addenda "
                "WHERE patient_id = CAST(:pid AS uuid) ORDER BY id",
                pid,
            ),
            "proposals": _rows(
                conn,
                "SELECT id::text FROM note_chart_proposals "
                "WHERE patient_id = CAST(:pid AS uuid) ORDER BY id",
                pid,
            ),
            "audit": _rows(
                conn,
                "SELECT id::text, user_id::text, action, resource_id, "
                "patient_id::text, changes::text AS changes FROM audit_logs ORDER BY id",
                {},
            ),
        }
        conn.rollback()
    return footprint


def _assert_nothing_of_xaviers(resp: Response, seed: _Seed) -> None:
    del seed
    for marker in (_X_LAST, _X_BODY, _X_ADDENDUM, "Avery Ames"):
        assert marker not in resp.text, marker


# ---------------------------------------------------------------------------
# Routes that take Avery's note or client by id
# ---------------------------------------------------------------------------

_SIGNER = {"signer_name": "Blake Byrne", "signer_credentials": "LMFT"}


@dataclass(frozen=True)
class _Route:
    name: str
    method: str
    path: Callable[[str, _Seed], str]
    #: Which of Avery's notes the route acts on: a draft, a signed note, or
    #: none (the route takes the client's id).
    state: str
    body: dict[str, Any] | None
    ok: int


def _note(suffix: str = "") -> Callable[[str, _Seed], str]:
    return lambda note_id, _s: f"/api/notes/{note_id}{suffix}"


def _client_notes(_note_id: str, s: _Seed) -> str:
    return f"/api/patients/{s.xavier}/notes"


_ROUTES = (
    _Route("read", "GET", _note(), "signed", None, 200),
    _Route("signing_record", "GET", _note("/signing"), "signed", None, 200),
    _Route(
        "edit",
        "PATCH",
        _note(),
        "draft",
        {
            "content_edited": {
                "subjective": _B_TEXT,
                "objective": "",
                "assessment": "",
                "plan": "",
            }
        },
        200,
    ),
    _Route("finalize", "POST", _note("/finalize"), "draft", {}, 200),
    _Route("sign", "POST", _note("/sign"), "draft", _SIGNER, 200),
    _Route("unlock", "POST", _note("/unlock"), "signed", {"reason": _B_TEXT}, 200),
    _Route("addendum", "POST", _note("/addenda"), "signed", {"text": _B_TEXT, **_SIGNER}, 201),
    _Route("client_notes_list", "GET", _client_notes, "none", None, 200),
    _Route("client_note_create", "POST", _client_notes, "none", {"note_type": "soap"}, 201),
)


def _targets(route: _Route, n: _Notes) -> tuple[str, str]:
    """(Avery's control note, Blake's target note) for ``route``."""
    if route.state == "draft":
        return n.draft_twin, n.draft
    return n.signed_twin, n.signed


def _send(client: TestClient, route: _Route, path: str) -> Response:
    resp: Response = client.request(route.method, path, json=route.body)
    return resp


def _assert_control(route: _Route, resp: Response) -> None:
    """Avery's call did what the route is for, so Blake's 404 is not vacuous."""
    assert resp.status_code == route.ok, (route.name, resp.status_code, resp.text[:500])
    body = resp.json()
    if route.name == "read":
        assert _X_BODY in resp.text
    elif route.name == "signing_record":
        assert body["signature"]["signer_name"] == "Avery Ames"
        assert [a["text"] for a in body["addenda"]] == [_X_ADDENDUM]
    elif route.name == "edit":
        assert body["content_edited"]["subjective"] == _B_TEXT
    elif route.name in {"finalize", "sign"}:
        assert body["finalized_at"] is not None
    elif route.name == "unlock":
        assert body["finalized_at"] is None
    elif route.name == "addendum":
        assert body["text"] == _B_TEXT
    elif route.name == "client_notes_list":
        assert body["total"] >= 4
        assert _X_BODY in resp.text
    elif route.name == "client_note_create":
        assert body["note_type"] == "soap"


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blake_cannot_reach_averys_note(  # noqa: PLR0913 — fixtures
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    notes: _Notes,
) -> None:
    twin, target = _targets(route, notes)

    # Control: the route works for the clinician who holds the grant, on a
    # note in the same state as the one Blake is about to try.
    control = _send(client, route, route.path(twin, seed))
    _assert_control(route, control)

    before = _footprint(engine, tenant_schema, seed)
    # Not vacuous: Avery's request was audited, so one by Blake would show.
    assert any(r["user_id"] == _AVERY for r in before["audit"])

    _as(caller, _BLAKE)
    denied = _send(client, route, route.path(target, seed))

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied, seed)
    # Nothing written: same notes, same signed versions, same addenda, no
    # proposals, and no audit row of a read or a change by Blake.
    assert _footprint(engine, tenant_schema, seed) == before


# ---------------------------------------------------------------------------
# Blake's own client: Blake is a working clinician, armed as themselves
# ---------------------------------------------------------------------------


def test_control_blake_reads_their_own_clients_notes(
    client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    _as(caller, _BLAKE)

    one = client.get(f"/api/notes/{seed.y_note}")
    listed = client.get(f"/api/patients/{seed.yolanda}/notes")

    assert one.status_code == 200, one.text[:500]
    assert _Y_BODY in one.text
    assert listed.status_code == 200, listed.text[:500]
    assert [n["id"] for n in listed.json()["data"]] == [seed.y_note]


# ---------------------------------------------------------------------------
# Ids that name nothing: Blake's 404 is a stranger's 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blakes_404_is_the_404_for_an_id_that_names_nothing(
    route: _Route, client: TestClient, caller: _Caller, seed: _Seed, notes: _Notes
) -> None:
    nobody = _Seed(xavier=str(uuid.uuid4()))
    made_up_note = str(uuid.uuid4())
    _, target = _targets(route, notes)
    _as(caller, _BLAKE)

    theirs = _send(client, route, route.path(target, seed))
    absent = _send(client, route, route.path(made_up_note, nobody))

    assert (theirs.status_code, absent.status_code) == (404, 404)
    raw = json.dumps(absent.json())
    for made_up, real in ((nobody.xavier, seed.xavier), (made_up_note, target)):
        raw = raw.replace(made_up, real)
    # Some 404s name the id they were asked for; the ids differ, and nothing else may.
    assert theirs.json() == json.loads(raw)
