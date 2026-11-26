# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-context chat, one clinician against another clinician's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and owns every conversation about him; Blake holds a grant on
Yolanda and none on Xavier. Every request below runs through the real app,
the real repositories and the real ``has_patient_access``, against a practice
schema provisioned the production way, on a connection that is subject to
row security. The request's principal is armed as whoever is calling, so a
check that leans on row security is tested as row security, not as the
caller the fixture happened to arm first.

Three kinds of case:

* **Routes on Avery's conversation** (read, rename, archive, purge, send a
  turn, save a turn as a note). Avery calls each one first and gets a 2xx;
  then Blake calls it on Avery's conversation and gets 404, never 403 or 409.
  After every write attempt the conversation, its messages, Xavier's notes
  and the practice audit trail are exactly as they were.
* **Routes that name Avery's client by id** (start a conversation, list
  conversations, preview a context). Same shape: Avery's control, then
  Blake's 404, and no conversation created.
* **Blake's own conversation, pointed at Avery's client's rows by id.** The
  turn and the preview accept explicit note and document ids. Blake names
  Avery's note and document alongside their own; the context handed to the
  model, and the manifest, carry Blake's rows and none of Xavier's. Avery's
  control proves the same request shape does carry those rows for someone
  holding the grant, so the absence is not vacuous.

The model is the fake gateway; assertions read the system prompt it was
handed (the retrieval layer's output), never the model's text.

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
    from app.services.chat_llm_gateway import FakeChatLLMGateway
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
# The chat router is mounted only when the feature is on; with it off every
# URL below 404s for everyone and the module would prove nothing.
os.environ.setdefault("ENABLE_PATIENT_CHAT", "true")

_TENANT_EMAIL = "e2e-cross-clinician-chat@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000a1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000b2"

# Distinctive enough that a substring match in a prompt is proof.
_X_NOTE_BODY = "xavier-progress-note-that-only-his-clinician-may-see"
_X_DOC_TEXT = "xavier-referral-letter-text-that-only-his-clinician-may-see"
_Y_NOTE_BODY = "yolanda-progress-note-her-own-clinician-reads"
_Y_DOC_TEXT = "yolanda-referral-letter-text-her-own-clinician-reads"
_ASSISTANT_TEXT = "Here is what the chart says."
_SYSTEM_PROMPT = "You are a clinical assistant for chart questions."


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_note: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_doc: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_note: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_doc: str = field(default_factory=lambda: str(uuid.uuid4()))


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


def _armed(conn: Connection, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client, a grant, a progress note and a document.

    Each clinician's rows are written as that clinician, so every row passes
    the same policy a request would have to.
    """
    s = _Seed()
    rows = (
        (_AVERY, s.xavier, "Xavier", s.x_note, _X_NOTE_BODY, s.x_doc, _X_DOC_TEXT),
        (_BLAKE, s.yolanda, "Yolanda", s.y_note, _Y_NOTE_BODY, s.y_doc, _Y_DOC_TEXT),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    for clinician, patient, first, note, body, doc, doc_text in rows:
        with engine.begin() as conn:
            _armed(conn, tenant_schema, clinician)
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, "
                    "session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :fn, 'Client', :fnl, 'client', "
                    "'active', 0, :now, :now)"
                ),
                {"pid": patient, "fn": first, "fnl": first.lower(), "now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient, "u": clinician},
            )
            conn.execute(
                text(
                    "INSERT INTO notes (id, patient_id, session_id, note_type, content, "
                    "status, author_user_id, restricted, created_at, updated_at, "
                    "finalized_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, 'soap', "
                    "CAST(:c AS jsonb), 'complete', CAST(:u AS uuid), false, "
                    ":now, :now, :now)"
                ),
                {
                    "id": note,
                    "pid": patient,
                    "u": clinician,
                    "c": json.dumps(
                        {"subjective": body, "objective": "", "assessment": "", "plan": ""}
                    ),
                    "now": now,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO patient_documents (id, patient_id, user_id, filename, "
                    "mime_type, gcs_path, extracted_text, size_bytes, category, "
                    "created_at, finalized_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:u AS uuid), "
                    "'referral.pdf', 'application/pdf', :gcs, :t, 1, 'chart', :now, :now)"
                ),
                {
                    "id": doc,
                    "pid": patient,
                    "u": clinician,
                    "gcs": f"patients/{patient}/referral.pdf",
                    "t": doc_text,
                    "now": now,
                },
            )
    return s


# ---------------------------------------------------------------------------
# Per-test client: one app, the caller switched per request
# ---------------------------------------------------------------------------


def _user(user_id: str, name: str) -> User:
    from app.models import User

    return User(
        id=user_id,
        email=_TENANT_EMAIL,
        name=name,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def caller() -> _Caller:
    return _Caller(user=_user(_AVERY, "Avery"))


@pytest.fixture
def fake_gateway() -> FakeChatLLMGateway:
    from app.services.chat_llm_gateway import (
        FakeChatLLMGateway,
        StreamEvent,
    )

    return FakeChatLLMGateway(
        script=[
            StreamEvent(delta=_ASSISTANT_TEXT),
            StreamEvent(finish_reason="stop", output_tokens=6),
        ]
    )


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    fake_gateway: FakeChatLLMGateway,
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
    from app.routes.chat import get_chat_llm_gateway
    from fastapi.testclient import TestClient

    _truncate_chat(engine, tenant_schema)

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
        return caller.user

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: caller.user.id
    fastapi_app.dependency_overrides[get_current_user] = _user_now
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _user_now
    fastapi_app.dependency_overrides[require_active_subscription] = _user_now
    fastapi_app.dependency_overrides[require_baa_acceptance] = _user_now
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_chat_llm_gateway] = lambda: fake_gateway

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()
        _truncate_chat(engine, tenant_schema)


def _truncate_chat(engine: Engine, schema: str) -> None:
    """Drop every conversation and the audit trail; keep the seeded charts.

    Notes created by save-as-note go too, so each test starts with exactly
    one note per client.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        # audit_logs is append-only; a fixture reset is an authorized purge.
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        conn.execute(text("TRUNCATE TABLE chat_messages, chat_conversations, audit_logs CASCADE"))
        conn.execute(text("DELETE FROM notes WHERE session_id IS NULL AND note_type = 'narrative'"))
        conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as(caller: _Caller, user_id: str) -> None:
    caller.user = _user(user_id, "Avery" if user_id == _AVERY else "Blake")


def _start(client: TestClient, patient_id: str, selection: dict[str, Any] | None = None) -> str:
    body: dict[str, Any] = {
        "patient_id": patient_id,
        "caller_feature_key": "chart_qa",
        "caller_system_prompt": _SYSTEM_PROMPT,
    }
    if selection is not None:
        body["default_source_selection"] = selection
    resp = client.post("/api/chat/conversations", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _turn(
    client: TestClient, conversation_id: str, selection: dict[str, Any] | None = None
) -> Response:
    body: dict[str, Any] = {"content": "What does the chart say?"}
    if selection is not None:
        body["source_selection"] = selection
    return client.post(f"/api/chat/conversations/{conversation_id}/messages", json=body)


def _assistant_message_id(engine: Engine, schema: str, conversation_id: str) -> str:
    with engine.begin() as conn:
        _armed(conn, schema, _AVERY)
        mid = conn.execute(
            text(
                "SELECT id FROM chat_messages WHERE conversation_id = CAST(:c AS uuid) "
                "AND role = 'assistant' ORDER BY sequence DESC LIMIT 1"
            ),
            {"c": conversation_id},
        ).scalar_one()
    return str(mid)


def _footprint(engine: Engine, schema: str, seed: _Seed) -> dict[str, Any]:
    """Everything a write by Blake could have touched, read as Avery.

    Avery holds the grant, so a row Blake changed is visible here. The audit
    trail is read under the purge GUC, the one setting that sees every
    actor's rows; without it the count would be Avery's rows only and
    Blake's would be invisible (``audit_logs`` is FORCE RLS).
    """
    with engine.connect() as conn:
        _armed(conn, schema, _AVERY)
        conversations = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT id::text, patient_id::text, owner_user_id::text, title, "
                    "caller_system_prompt, archived_at, last_turn_at, "
                    "default_source_selection::text AS sel FROM chat_conversations "
                    "ORDER BY id"
                )
            ).mappings()
        ]
        messages = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT id::text, conversation_id::text, sequence, role, content, "
                    "context_manifest::text AS manifest "
                    "FROM chat_messages ORDER BY conversation_id, sequence"
                )
            ).mappings()
        ]
        notes = conn.execute(
            text("SELECT count(*) FROM notes WHERE patient_id = CAST(:p AS uuid)"),
            {"p": seed.xavier},
        ).scalar_one()
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        audit = [
            dict(r)
            for r in conn.execute(
                text("SELECT id::text, user_id::text, action FROM audit_logs ORDER BY id")
            ).mappings()
        ]
        conn.rollback()
    return {"conversations": conversations, "messages": messages, "notes": notes, "audit": audit}


# ---------------------------------------------------------------------------
# Routes on Avery's conversation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConversationRoute:
    name: str
    method: str
    path: str  # ``{c}`` = conversation id, ``{m}`` = assistant message id
    ok: int
    body: dict[str, Any] | None = None
    # A control that changes the conversation runs on a twin, so Blake's
    # attempt meets the conversation in the state Avery left it.
    control_on_twin: bool = False


_CONVERSATION_ROUTES = (
    _ConversationRoute("get", "GET", "/api/chat/conversations/{c}", 200),
    _ConversationRoute(
        "patch_title",
        "PATCH",
        "/api/chat/conversations/{c}",
        200,
        body={"title": "Renamed by whoever called"},
        control_on_twin=True,
    ),
    _ConversationRoute(
        "patch_archive",
        "PATCH",
        "/api/chat/conversations/{c}",
        200,
        body={"archive": True},
        control_on_twin=True,
    ),
    _ConversationRoute(
        "delete_archive",
        "DELETE",
        "/api/chat/conversations/{c}?mode=archive",
        204,
        control_on_twin=True,
    ),
    _ConversationRoute(
        "delete_purge",
        "DELETE",
        "/api/chat/conversations/{c}?mode=purge",
        204,
        control_on_twin=True,
    ),
    _ConversationRoute(
        "send_message",
        "POST",
        "/api/chat/conversations/{c}/messages",
        200,
        body={"content": "Tell me about this client."},
        control_on_twin=True,
    ),
    _ConversationRoute(
        "save_as_note",
        "POST",
        "/api/chat/conversations/{c}/messages/{m}/save-as-note",
        201,
        control_on_twin=True,
    ),
)


@pytest.mark.parametrize("route", _CONVERSATION_ROUTES, ids=lambda r: r.name)
def test_blake_cannot_reach_averys_conversation(
    route: _ConversationRoute,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    fake_gateway: FakeChatLLMGateway,
) -> None:
    # Avery's conversation about Xavier, with one turn in it.
    target = _start(client, seed.xavier)
    assert _turn(client, target).status_code == 200
    twin = _start(client, seed.xavier)
    assert _turn(client, twin).status_code == 200

    def call(conversation_id: str) -> Response:
        path = route.path.format(
            c=conversation_id,
            m=_assistant_message_id(engine, tenant_schema, conversation_id),
        )
        return client.request(route.method, path, json=route.body)

    # Control: the route works for the clinician who holds the grant.
    control = call(twin if route.control_on_twin else target)
    assert control.status_code == route.ok, control.text

    before = _footprint(engine, tenant_schema, seed)
    # Not vacuous: the footprint sees the conversation, its turns and the
    # audit rows Avery's own calls wrote.
    assert target in {c["id"] for c in before["conversations"]}
    assert before["messages"]
    assert before["audit"]
    model_calls = len(fake_gateway.calls)

    _as(caller, _BLAKE)
    denied = call(target)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text)
    assert _X_NOTE_BODY not in denied.text
    assert _ASSISTANT_TEXT not in denied.text
    # Nothing written: same conversation, same messages, no new note on
    # Xavier, no audit row of a change, and the model was never called.
    assert _footprint(engine, tenant_schema, seed) == before
    assert len(fake_gateway.calls) == model_calls

    # The target is still Avery's, intact and readable by Avery.
    _as(caller, _AVERY)
    assert client.get(f"/api/chat/conversations/{target}").status_code == 200


# ---------------------------------------------------------------------------
# Routes that name Avery's client
# ---------------------------------------------------------------------------


def _create_on(client: TestClient, patient_id: str) -> Response:
    return client.post(
        "/api/chat/conversations",
        json={
            "patient_id": patient_id,
            "caller_feature_key": "chart_qa",
            "caller_system_prompt": _SYSTEM_PROMPT,
        },
    )


def _list_on(client: TestClient, patient_id: str) -> Response:
    return client.get(f"/api/chat/conversations?patient_id={patient_id}&include_archived=true")


def _preview_on(client: TestClient, patient_id: str) -> Response:
    return client.post(
        "/api/chat/conversations/preview",
        json={"patient_id": patient_id, "source_selection": {"progress_notes_recent": True}},
    )


_PATIENT_ROUTES: tuple[tuple[str, Callable[[TestClient, str], Response], int], ...] = (
    ("create_conversation", _create_on, 201),
    ("list_conversations", _list_on, 200),
    ("preview_context", _preview_on, 200),
)


@pytest.mark.parametrize(
    ("name", "call", "ok"), _PATIENT_ROUTES, ids=[r[0] for r in _PATIENT_ROUTES]
)
def test_blake_cannot_name_averys_client(
    name: str,
    call: Callable[[TestClient, str], Response],
    ok: int,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
) -> None:
    # An existing conversation about Xavier, so the list has something to
    # leak and the preview has a note to read.
    _start(client, seed.xavier)

    control = call(client, seed.xavier)
    assert control.status_code == ok, control.text
    if name == "list_conversations":
        assert control.json()["total"] >= 1
    if name == "preview_context":
        assert seed.x_note in json.dumps(control.json()["manifest"])

    before = _footprint(engine, tenant_schema, seed)
    _as(caller, _BLAKE)
    denied = call(client, seed.xavier)

    assert denied.status_code == 404, (name, denied.status_code, denied.text)
    assert seed.x_note not in denied.text
    assert "Xavier" not in denied.text
    assert _footprint(engine, tenant_schema, seed) == before


# ---------------------------------------------------------------------------
# Blake's own conversation, pointed at Avery's client's rows
# ---------------------------------------------------------------------------


def _explicit(seed: _Seed, *, mine: tuple[str, str], theirs: tuple[str, str]) -> dict[str, Any]:
    """Name both clients' note and document by id, in one selection."""
    return {
        "progress_notes_explicit": {"note_ids": [mine[0], theirs[0]]},
        "patient_documents": {"document_ids": [mine[1], theirs[1]]},
    }


def test_control_avery_names_xaviers_rows_and_the_model_sees_them(
    client: TestClient, seed: _Seed, fake_gateway: FakeChatLLMGateway
) -> None:
    """The request shape below does carry a named row into the prompt."""
    selection = _explicit(seed, mine=(seed.x_note, seed.x_doc), theirs=(seed.y_note, seed.y_doc))
    conversation = _start(client, seed.xavier)

    assert _turn(client, conversation, selection).status_code == 200

    prompt = fake_gateway.calls[-1]["system_prompt"]
    assert _X_NOTE_BODY in prompt
    assert _X_DOC_TEXT in prompt
    # And Avery naming Yolanda's rows gets none of them either.
    assert _Y_NOTE_BODY not in prompt
    assert _Y_DOC_TEXT not in prompt


def test_blakes_turn_carries_none_of_xaviers_rows(
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    fake_gateway: FakeChatLLMGateway,
) -> None:
    selection = _explicit(seed, mine=(seed.y_note, seed.y_doc), theirs=(seed.x_note, seed.x_doc))
    _as(caller, _BLAKE)
    conversation = _start(client, seed.yolanda)

    resp = _turn(client, conversation, selection)

    assert resp.status_code == 200, resp.text
    prompt = fake_gateway.calls[-1]["system_prompt"]
    # Visibility control: Blake's own rows are in the context.
    assert _Y_NOTE_BODY in prompt
    assert _Y_DOC_TEXT in prompt
    # None of Xavier's.
    assert _X_NOTE_BODY not in prompt
    assert _X_DOC_TEXT not in prompt
    assert "Xavier" not in prompt
    assert _X_NOTE_BODY not in resp.text

    # The manifest stored on Blake's turn names no row of Xavier's.
    with engine.connect() as conn:
        _armed(conn, tenant_schema, _BLAKE)
        manifests = conn.execute(
            text(
                "SELECT context_manifest::text FROM chat_messages "
                "WHERE conversation_id = CAST(:c AS uuid) AND role = 'user'"
            ),
            {"c": conversation},
        ).scalars()
        stored = " ".join(m or "" for m in manifests)
        conn.rollback()
    assert seed.y_note in stored
    assert seed.x_doc not in stored
    # A named note that was not found is reported back as missing by id;
    # that is the id Blake sent, not a row of Xavier's.
    assert _X_NOTE_BODY not in stored


def test_blakes_conversation_default_selection_carries_none_of_xaviers_rows(
    client: TestClient, caller: _Caller, seed: _Seed, fake_gateway: FakeChatLLMGateway
) -> None:
    """The ids can also ride in on the conversation's stored default."""
    selection = _explicit(seed, mine=(seed.y_note, seed.y_doc), theirs=(seed.x_note, seed.x_doc))
    _as(caller, _BLAKE)
    conversation = _start(client, seed.yolanda, selection)

    assert _turn(client, conversation).status_code == 200

    prompt = fake_gateway.calls[-1]["system_prompt"]
    assert _Y_NOTE_BODY in prompt
    assert _X_NOTE_BODY not in prompt
    assert _X_DOC_TEXT not in prompt


def test_blakes_preview_lists_none_of_xaviers_rows(
    client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    selection = _explicit(seed, mine=(seed.y_note, seed.y_doc), theirs=(seed.x_note, seed.x_doc))

    control = client.post(
        "/api/chat/conversations/preview",
        json={
            "patient_id": seed.xavier,
            "source_selection": _explicit(
                seed, mine=(seed.x_note, seed.x_doc), theirs=(seed.y_note, seed.y_doc)
            ),
        },
    )
    assert control.status_code == 200, control.text
    assert seed.x_doc in json.dumps(control.json()["manifest"])

    _as(caller, _BLAKE)
    resp = client.post(
        "/api/chat/conversations/preview",
        json={"patient_id": seed.yolanda, "source_selection": selection},
    )

    assert resp.status_code == 200, resp.text
    manifest = json.dumps(resp.json()["manifest"])
    assert seed.y_note in manifest
    assert seed.y_doc in manifest
    assert seed.x_doc not in manifest
    assert _X_NOTE_BODY not in resp.text
    assert _X_DOC_TEXT not in resp.text
