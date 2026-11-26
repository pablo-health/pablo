# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Portal message threads, one clinician against another clinician's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier, and Xavier's portal threads are Avery's to answer. Blake holds a
grant on Yolanda, who has a thread of her own with a message waiting, and none
on Xavier, so Blake is a working clinician throughout and a 404 below is never
a broken account. Every request runs through the real app, the real
repositories and the real ``has_patient_access``, against a practice schema
provisioned the production way, on a connection subject to row security. The
request's principal is armed as whoever is calling.

Stood in: the patient notice a reply sends and the hooks a reply dispatches
(recorders, so a reply that would have emailed Xavier or reached a
deployment's mirror shows up).

Three kinds of case:

* **Every clinician route on one of Avery's threads** (read, reply, close,
  reopen, assign, mark read, export, and the per-client thread list). Avery
  calls each one first and gets a 2xx; then Blake calls it on Avery's thread
  and gets 404, never 403 or 409, with nothing of Xavier's in the body. A write
  route's control runs on a sibling thread, so the thread Blake aims at is
  still in the state the write would change: open, unassigned, unread. After
  Blake's attempt every thread, message and attachment row, the inbox state,
  the audit trail, the notices and the hooks are exactly as they were.
* **The inbox lists** (threads, single messages, the unread badge). These do
  not 404: Blake's answer leaves Xavier out. Avery's list carries Xavier's
  thread first, and Blake's carries Yolanda's, so an empty list is never what
  passes.
* **Lookups by an id that names nothing**, so a 404 for Blake is the same 404
  a stranger gets.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.models import User
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

_TENANT_EMAIL = "e2e-cross-clinician-message-threads@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000e1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000e2"

# Distinctive enough that a substring match in a body is proof. Letters only:
# a sentinel made of digits would turn up by chance in ids and timestamps.
_X_SUBJECT = "Quillfeatherx rescheduling"
_X_BODY = "Quillfeatherx asks about Thursday"
_X_REPLY = "Quillfeatherx practice answer"
_Y_SUBJECT = "Marrowgatey question"
_Y_BODY = "Marrowgatey asks about forms"
_INTRUDER = "Intrudingblake words"

_X_MARKERS = (_X_SUBJECT, _X_BODY, _X_REPLY, "Xavier")


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Avery's thread with Xavier that Blake aims at: open, unassigned, with a
    # client message the practice has not read.
    x_thread: str = field(default_factory=lambda: str(uuid.uuid4()))
    # The same, for Avery's write controls, so x_thread stays untouched.
    x_sibling: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Two closed threads: Blake tries to reopen one, Avery reopens the other.
    x_closed: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_closed_sibling: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Xavier's unread message in x_thread, which a reply would answer.
    x_message: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_thread: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_message: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class _Caller:
    """Who the next request is from. Every auth override reads this."""

    user: User


@dataclass
class _Outbox:
    """Notices to a patient and hooks dispatched, in order."""

    notices: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Module-scoped infra
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars as this module found them."""
    from app.db import (
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
    from tests_integration.database.test_tenant_resolution_db import (
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


def _scoped(conn: Connection, schema: str, user_id: str) -> None:
    """Put ``conn``'s transaction on the practice schema, armed as ``user_id``."""
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


_INSERT_THREAD = text(
    "INSERT INTO patient_message_threads (id, patient_id, subject, status, closed_at, "
    "closed_by, created_at, last_message_at) VALUES (CAST(:id AS uuid), "
    "CAST(:pid AS uuid), :subject, :status, :closed_at, CAST(:closed_by AS uuid), :at, :at)"
)
_INSERT_MESSAGE = text(
    "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, created_at) "
    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:pid AS uuid), :sender, :body, :at)"
)


@pytest.fixture
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client and the threads with each, fresh per test.

    Fresh ids every time, so no test sees another's threads and none needs
    cleaning up. Each clinician's rows are written as that clinician, so every
    row passes the same policy a request would have to.

    Every thread opens with a client message the practice has not read, and
    Avery's threads also carry an earlier practice reply, so a read, an
    export or a list has words of Xavier's to show.
    """
    s = _Seed()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
    closed = {s.x_closed, s.x_closed_sibling}
    charts = (
        (_AVERY, s.xavier, "Xavier", (s.x_thread, s.x_sibling, *sorted(closed))),
        (_BLAKE, s.yolanda, "Yolanda", (s.y_thread,)),
    )
    for clinician, patient, first, threads in charts:
        with engine.begin() as conn:
            _scoped(conn, tenant_schema, clinician)
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, email, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :fn, 'Doe', :fnl, 'doe', :email, 'active', 0, "
                    "now(), now())"
                ),
                {"pid": patient, "fn": first, "fnl": first.lower(), "email": f"{first}@x.test"},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient, "u": clinician},
            )
            for thread in threads:
                is_closed = thread in closed
                ours = clinician == _AVERY
                conn.execute(
                    _INSERT_THREAD,
                    {
                        "id": thread,
                        "pid": patient,
                        "subject": _X_SUBJECT if ours else _Y_SUBJECT,
                        "status": "closed" if is_closed else "open",
                        "closed_at": at if is_closed else None,
                        "closed_by": clinician if is_closed else None,
                        "at": at,
                    },
                )
                if ours:
                    conn.execute(
                        _INSERT_MESSAGE,
                        {
                            "id": str(uuid.uuid4()),
                            "tid": thread,
                            "pid": patient,
                            "sender": "clinician",
                            "body": _X_REPLY,
                            "at": at,
                        },
                    )
                first_message = {s.x_thread: s.x_message, s.y_thread: s.y_message}
                conn.execute(
                    _INSERT_MESSAGE,
                    {
                        "id": first_message.get(thread, str(uuid.uuid4())),
                        "tid": thread,
                        "pid": patient,
                        "sender": "patient",
                        "body": _X_BODY if ours else _Y_BODY,
                        "at": at + timedelta(hours=1),
                    },
                )
    return s


# ---------------------------------------------------------------------------
# Per-test client: one app, the caller switched per request
# ---------------------------------------------------------------------------


def _user(user_id: str) -> User:
    from app.models import User

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
def outbox(monkeypatch: pytest.MonkeyPatch) -> _Outbox:
    """Record, where the reply route calls them, the notice and the hooks.

    Patched on the route module: the notice is what tells Xavier something
    is waiting, and the hooks are what a deployment mirroring the
    conversation would receive. Either one from Blake's request is a leak
    even if no row changed.
    """
    box = _Outbox()

    def _notify(_delivery: object, _messages: object, *, patient_id: str, **_kw: Any) -> bool:
        box.notices.append(patient_id)
        return True

    def _dispatch(event: Any) -> None:
        box.hooks.append(event.thread_id)

    monkeypatch.setattr("app.routes.patient_messages.notify_patient_of_reply", _notify)
    monkeypatch.setattr("app.routes.patient_messages.dispatch_patient_message", _dispatch)
    return box


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    outbox: _Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    from app.auth.providers import VerifiedIdentity
    from app.auth.service import (
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session
    from fastapi.testclient import TestClient

    del seed, outbox  # requested for their setup only
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
        # The real user dependency arms the RLS principal; overriding it must
        # keep the arming, or a control would read nothing for the wrong
        # reason. Most routes here depend on nothing else that arms.
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
    """Empty the audit trail; keep the seeded threads."""
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


def _audit(engine: Engine, schema: str) -> list[dict[str, Any]]:
    """Every audit row, whoever wrote it.

    Read under the purge GUC, the one setting that sees every actor's rows;
    without it the read would return the armed clinician's rows only and
    Blake's would be invisible (``audit_logs`` is FORCE RLS).
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT id::text, user_id::text, action, resource_id, "
                    "patient_id::text, changes::text AS changes FROM audit_logs ORDER BY id"
                )
            ).mappings()
        ]
        conn.rollback()
    return rows


# The rows a thread route could write. Each is read whole, as text, so a
# changed status, assignee, closed_by or last_message_at shows, and so does
# a new message, attachment link, read mark or inbox state.
_FOOTPRINT_TABLES = (
    "patient_message_threads",
    "patient_messages",
    "patient_message_attachments",
    "patient_message_thread_reads",
    "inbox_item_states",
)


def _rows(engine: Engine, schema: str) -> dict[str, list[str]]:
    """Every row of every table above, read under each clinician's arm.

    A row on Xavier is Avery's to see, and a row only Blake may see (an inbox
    state Blake wrote) is Blake's, so between the two arms anything either of
    them could have written or changed is in here.
    """
    seen: dict[str, list[str]] = {}
    for who in (_AVERY, _BLAKE):
        with engine.connect() as conn:
            _scoped(conn, schema, who)
            for table in _FOOTPRINT_TABLES:
                # ``table`` comes from the constant above, never from input.
                query = text(f"SELECT t::text FROM {table} t ORDER BY 1")  # noqa: S608
                seen[f"{who}:{table}"] = [r[0] for r in conn.execute(query)]
            conn.rollback()
    return seen


def _footprint(engine: Engine, schema: str, outbox: _Outbox) -> dict[str, Any]:
    return {
        "rows": _rows(engine, schema),
        "audit": _audit(engine, schema),
        "notices": list(outbox.notices),
        "hooks": list(outbox.hooks),
    }


def _assert_nothing_of_xaviers(resp: Response) -> None:
    """No subject, no words, no name. A 404 may echo the id it was asked for."""
    for marker in _X_MARKERS:
        assert marker not in resp.text, marker


# ---------------------------------------------------------------------------
# Every route on one thread, Blake against Avery's
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Route:
    name: str
    method: str
    # The path Blake is refused on.
    path: Callable[[_Seed], str]
    # The path Avery's control runs on, where the route changes its thread.
    control_path: Callable[[_Seed], str] | None = None
    body: Callable[[_Seed], dict[str, Any]] | None = None
    # What Avery's control must carry, so Blake's 404 is not vacuous.
    shows: Callable[[_Seed], str] = lambda s: s.x_thread


def _thread(name: str, verb: str = "") -> Callable[[_Seed], str]:
    attr = {"thread": "x_thread", "sibling": "x_sibling"}[name]
    return lambda s: f"/api/message-threads/{getattr(s, attr)}{verb}"


_ROUTES = (
    _Route("read", "GET", _thread("thread"), shows=lambda _s: _X_BODY),
    _Route("export", "GET", _thread("thread", "/export"), shows=lambda _s: _X_REPLY),
    _Route(
        "patient-threads",
        "GET",
        lambda s: f"/api/patients/{s.xavier}/message-threads",
        shows=lambda _s: _X_SUBJECT,
    ),
    _Route(
        "reply",
        "POST",
        _thread("thread", "/replies"),
        control_path=_thread("sibling", "/replies"),
        body=lambda _s: {"body": _INTRUDER},
        shows=lambda _s: _INTRUDER,
    ),
    _Route(
        "reply-to-message",
        "POST",
        _thread("thread", "/replies"),
        control_path=_thread("thread", "/replies"),
        body=lambda s: {"body": _INTRUDER, "in_reply_to_message_id": s.x_message},
        shows=lambda _s: _INTRUDER,
    ),
    _Route(
        "close",
        "POST",
        _thread("thread", "/close"),
        control_path=_thread("sibling", "/close"),
        shows=lambda _s: '"status":"closed"',
    ),
    _Route(
        "reopen",
        "POST",
        lambda s: f"/api/message-threads/{s.x_closed}/reopen",
        control_path=lambda s: f"/api/message-threads/{s.x_closed_sibling}/reopen",
        shows=lambda _s: '"status":"open"',
    ),
    _Route(
        "assign",
        "POST",
        _thread("thread", "/assign"),
        control_path=_thread("sibling", "/assign"),
        body=lambda _s: {"user_id": _BLAKE},
        shows=lambda _s: f'"assigned_user_id":"{_BLAKE}"',
    ),
    _Route(
        "read-mark",
        "POST",
        _thread("thread", "/read"),
        control_path=_thread("sibling", "/read"),
        shows=lambda s: s.x_sibling,
    ),
)


def _send(
    client: TestClient,
    method: str,
    path: str,
    body: Callable[[_Seed], dict[str, Any]] | None,
    seed: _Seed,
) -> Response:
    resp: Response = client.request(method, path, json=body(seed) if body else None)
    return resp


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blake_cannot_reach_averys_thread(
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    outbox: _Outbox,
) -> None:
    # Blake's attempt runs FIRST on the routes whose control would use up
    # the very thread Blake aims at (a reply naming Xavier's message).
    blake_first = route.control_path is not None and route.control_path(seed) == route.path(seed)

    if blake_first:
        before = _footprint(engine, tenant_schema, outbox)
        _as(caller, _BLAKE)
        denied = _send(client, route.method, route.path(seed), route.body, seed)
        after = _footprint(engine, tenant_schema, outbox)
        _as(caller, _AVERY)

    # Control: the route serves the clinician who holds the grant.
    control_path = (route.control_path or route.path)(seed)
    rows_before_control = _rows(engine, tenant_schema)
    control = _send(client, route.method, control_path, route.body, seed)
    assert 200 <= control.status_code < 300, (route.name, control.status_code, control.text[:500])
    assert route.shows(seed) in control.text, (route.name, control.text[:500])
    if route.method == "POST":
        # Not vacuous: the same write from Avery shows in the rows compared below.
        assert _rows(engine, tenant_schema) != rows_before_control, route.name
    # Not vacuous: Avery's call was audited, so one by Blake would show too.
    assert any(r["user_id"] == _AVERY for r in _audit(engine, tenant_schema))

    if not blake_first:
        before = _footprint(engine, tenant_schema, outbox)
        _as(caller, _BLAKE)
        denied = _send(client, route.method, route.path(seed), route.body, seed)
        after = _footprint(engine, tenant_schema, outbox)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied)
    # Nothing written: no message, no status, assignee or read mark changed,
    # no inbox state, no audit row, no notice to Xavier, no hook.
    assert after == before


def test_a_reply_from_avery_does_notify_and_dispatch(
    client: TestClient, seed: _Seed, outbox: _Outbox
) -> None:
    """The recorders see a real reply, so their silence on Blake's means something."""
    resp = client.post(f"/api/message-threads/{seed.x_sibling}/replies", json={"body": "ok"})

    assert resp.status_code == 201, resp.text[:500]
    assert outbox.notices == [seed.xavier]
    assert outbox.hooks == [seed.x_sibling]


def test_blake_still_reaches_their_own_client(
    client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    """Blake is a working clinician: the same routes answer on Yolanda's thread."""
    _as(caller, _BLAKE)

    read = client.get(f"/api/message-threads/{seed.y_thread}")
    listed = client.get(f"/api/patients/{seed.yolanda}/message-threads")
    exported = client.get(f"/api/message-threads/{seed.y_thread}/export")

    assert read.status_code == 200, read.text[:500]
    assert _Y_BODY in read.text
    assert listed.status_code == 200, listed.text[:500]
    assert [t["id"] for t in listed.json()["data"]] == [seed.y_thread]
    assert exported.status_code == 200, exported.text[:500]
    assert _Y_BODY in exported.text


# ---------------------------------------------------------------------------
# The inbox lists: Xavier is left out, not refused
# ---------------------------------------------------------------------------


def _avery_threads(s: _Seed) -> set[str]:
    return {s.x_thread, s.x_sibling, s.x_closed, s.x_closed_sibling}


# The schema lives for the module and every test seeds afresh, so each list
# also carries earlier tests' threads. The assertions are on this test's ids,
# plus the sentinels, which every one of Avery's threads carries.


def test_the_inbox_lists_avery_and_blake_their_own_threads(
    client: TestClient, caller: _Caller, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    """Every status: Avery's list carries Xavier's four threads, Blake's none of them."""
    seen: dict[str, Response] = {}
    for who in (_AVERY, _BLAKE):
        _as(caller, who)
        seen[who] = client.get("/api/message-threads", params={"status": "all"})
        assert seen[who].status_code == 200, seen[who].text[:500]
    ids = {who: {t["id"] for t in resp.json()["data"]} for who, resp in seen.items()}

    assert _avery_threads(seed) <= ids[_AVERY]
    assert seed.y_thread not in ids[_AVERY]
    assert seed.y_thread in ids[_BLAKE]
    assert not _avery_threads(seed) & ids[_BLAKE]
    _assert_nothing_of_xaviers(seen[_BLAKE])
    # Blake's list was recorded as a read of Blake's own clients' correspondence.
    blakes = {r["patient_id"] for r in _audit(engine, tenant_schema) if r["user_id"] == _BLAKE}
    assert seed.yolanda in blakes
    assert seed.xavier not in blakes


def test_the_message_list_shows_blake_none_of_xaviers_words(
    client: TestClient, caller: _Caller, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    _as(caller, _AVERY)
    averys = client.get("/api/message-threads/messages")
    assert averys.status_code == 200, averys.text[:500]
    assert seed.x_message in {m["id"] for m in averys.json()["data"]}
    assert _X_BODY in averys.text

    _as(caller, _BLAKE)
    blakes = client.get("/api/message-threads/messages")

    assert blakes.status_code == 200, blakes.text[:500]
    blake_ids = {m["id"] for m in blakes.json()["data"]}
    assert seed.y_message in blake_ids
    assert seed.x_message not in blake_ids
    _assert_nothing_of_xaviers(blakes)
    blake_rows = [r for r in _audit(engine, tenant_schema) if r["user_id"] == _BLAKE]
    read = {(r["resource_id"], r["patient_id"]) for r in blake_rows}
    assert (seed.y_thread, seed.yolanda) in read
    assert seed.xavier not in {r["patient_id"] for r in blake_rows}


def _badges(client: TestClient, caller: _Caller) -> tuple[int, int]:
    counts = []
    for who in (_AVERY, _BLAKE):
        _as(caller, who)
        resp = client.get("/api/message-threads/unread-count")
        assert resp.status_code == 200, resp.text[:500]
        counts.append(resp.json()["threads_with_unread"])
    return counts[0], counts[1]


def test_the_unread_badge_counts_none_of_xaviers_threads(
    client: TestClient, caller: _Caller, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    """Xavier's threads move Avery's badge and never Blake's; Yolanda's move Blake's."""
    avery, blake = _badges(client, caller)

    # Avery reads one of Xavier's open threads: one fewer for Avery only.
    _as(caller, _AVERY)
    assert client.post(f"/api/message-threads/{seed.x_thread}/read").status_code == 200
    assert _badges(client, caller) == (avery - 1, blake)

    # Xavier writes again: it is unread for Avery once more, still not for Blake.
    with engine.begin() as conn:
        _scoped(conn, tenant_schema, _AVERY)
        conn.execute(
            _INSERT_MESSAGE,
            {
                "id": str(uuid.uuid4()),
                "tid": seed.x_thread,
                "pid": seed.xavier,
                "sender": "patient",
                "body": _X_BODY,
                "at": datetime.now(UTC) + timedelta(minutes=1),
            },
        )
    assert _badges(client, caller) == (avery, blake)

    # Blake's badge is live: reading Yolanda's thread takes it down by one.
    _as(caller, _BLAKE)
    assert client.post(f"/api/message-threads/{seed.y_thread}/read").status_code == 200
    assert _badges(client, caller) == (avery, blake - 1)


# ---------------------------------------------------------------------------
# Ids that name nothing: Blake's 404 is a stranger's 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blakes_404_is_the_404_for_an_id_that_names_nothing(
    route: _Route, client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    nobody = _Seed()
    _as(caller, _BLAKE)

    theirs = _send(client, route.method, route.path(seed), route.body, seed)
    absent = _send(client, route.method, route.path(nobody), route.body, nobody)

    assert (theirs.status_code, absent.status_code) == (404, 404)
    assert theirs.json() == _with_ids(absent.json(), nobody, seed)


def _with_ids(body: Any, nobody: _Seed, seed: _Seed) -> Any:
    """``body`` with the made-up ids swapped for the real ones.

    Some 404s name the id they were asked for; the request ids differ, and
    nothing else may.
    """
    raw = json.dumps(body)
    for made_up, real in (
        (nobody.xavier, seed.xavier),
        (nobody.x_thread, seed.x_thread),
        (nobody.x_closed, seed.x_closed),
    ):
        raw = raw.replace(made_up, real)
    return json.loads(raw)
