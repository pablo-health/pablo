# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sessions, transcripts, audio and dictations, one clinician against another's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and recorded every session on his chart. Blake holds a grant on
Yolanda, with a session of her own, and none on Xavier. Every request runs
through the real app, the real repositories and the real
``has_patient_access``, against a practice schema provisioned the production
way, on a connection subject to row security. The request's principal is
armed as whoever is calling.

Each test gets fresh sessions on Xavier's chart, as Avery, in the three
states the routes act on: a recording that has finished and waits for its
audio or transcript (with both audio channels already in storage, so the
finalize step would go through), a drafted note waiting for review (with a
dictation on it), and a signed note. Every state has two sessions: a twin for
Avery's control and a target for Blake, so Blake's attempt is made on a
session the action would succeed on, if Blake could see it.

Object storage is a local directory and the task queue a list, both
recorded: no bucket, no transcription provider. A route that minted an upload
URL, stored a byte or queued a job for Blake shows up in the footprint.

Three kinds of case:

* **Routes that take Avery's session or client by id** (the session detail,
  a transcript, the multipart audio upload, the signed-URL upload's init and
  finalize, finalizing the session, a new session for the client, and the
  dictation list and new dictation). Avery calls each one first and it
  works; then Blake calls it on Avery's object and gets 404, never 403 or
  409, with nothing of Xavier's in the body. Afterwards every session, note,
  signed version, addendum and dictation row on Xavier's chart, the audit
  trail, the stored objects, the minted upload URLs and the queued jobs are
  exactly as they were before Blake's request.
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
from app.services.file_storage import LocalFileStorage, UploadTarget
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

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

_TENANT_EMAIL = "e2e-cross-clinician-sessions@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000e1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000e2"

# Distinctive enough that a substring match in a body is proof. Letters only:
# a sentinel made of digits would turn up by chance in ids and timestamps.
_X_LAST = "Thistlewaitex"
_X_TRANSCRIPT = "Xavier talked about the lighthouse keeper"
_X_BODY = "Xavier described the harbor dream again"
_X_DICTATION = "Xavier asked to move to mornings"
_Y_LAST = "Brackenmoory"
_Y_BODY = "Yolanda kept the sleep diary all week"
# What Blake tries to write. If any of it reaches a row, the footprint shows it.
_B_TEXT = "Blake was here"

# A WAV header passes the upload routes' container sniff; the rest is padding.
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 48


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_session: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class _Sessions:
    """Fresh sessions on Xavier's chart, all Avery's, for one test."""

    recorded_twin: str
    recorded: str
    review_twin: str
    review: str
    signed_twin: str
    signed: str


@dataclass
class _Caller:
    """Who the next request is from. Every auth override reads this."""

    user: User


class _RecordingStorage(LocalFileStorage):
    """Local storage that can also mint upload URLs, and keeps a list of them."""

    def __init__(self) -> None:
        self.minted: list[str] = []

    def make_upload_target(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> UploadTarget:
        del bucket, content_type, max_bytes, ttl_seconds
        self.minted.append(object_name)
        return UploadTarget(url=f"https://storage.invalid/{object_name}", method="PUT")


@dataclass
class _Outside:
    """What the routes did outside the database: objects, URLs, queued jobs."""

    bucket: Path
    storage: _RecordingStorage = field(default_factory=_RecordingStorage)
    jobs: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        objects = sorted(
            str(p.relative_to(self.bucket)) for p in self.bucket.rglob("*") if p.is_file()
        )
        return {
            "objects": objects,
            "minted": list(self.storage.minted),
            "jobs": json.loads(json.dumps(self.jobs)),
        }


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
    """Each clinician's client; Blake's with a reviewed session of Blake's own."""
    s = _Seed()
    _seed_patient(engine, tenant_schema, _AVERY, s.xavier, "Xavier", _X_LAST)
    _seed_patient(engine, tenant_schema, _BLAKE, s.yolanda, "Yolanda", _Y_LAST)
    session, tokens = _open_session(engine, tenant_schema, _BLAKE)
    try:
        _insert_session(session, s.y_session, s.yolanda, _BLAKE, "pending_review")
        _insert_note(session, str(uuid.uuid4()), s.y_session, s.yolanda, _BLAKE, _Y_BODY)
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


def _insert_session(
    session: Session, session_id: str, patient_id: str, clinician: str, status: str
) -> None:
    session.execute(
        text(
            "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
            "session_number, status, transcript, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), now(), "
            "1, :status, CAST(:tr AS jsonb), now(), now())"
        ),
        {
            "id": session_id,
            "u": clinician,
            "pid": patient_id,
            "status": status,
            "tr": json.dumps({"format": "txt", "content": _X_TRANSCRIPT}),
        },
    )


def _insert_note(  # noqa: PLR0913 — one row's columns
    session: Session, note_id: str, session_id: str, patient_id: str, author: str, body: str
) -> None:
    """The session's drafted SOAP note."""
    session.execute(
        text(
            "INSERT INTO notes (id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:sid AS uuid), 'soap', "
            "CAST(:c AS jsonb), 'complete', CAST(:author AS uuid), false, now(), now())"
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "sid": session_id,
            "c": json.dumps({"subjective": body, "objective": "", "assessment": "", "plan": ""}),
            "author": author,
        },
    )


def _insert_dictation(session: Session, session_id: str, note_id: str, patient_id: str) -> None:
    session.execute(
        text(
            "INSERT INTO session_dictations (id, session_id, note_id, patient_id, "
            "author_user_id, audio_path, content_type, status, transcript, used_as, "
            "created_at, transcribed_at) "
            "VALUES (gen_random_uuid(), CAST(:sid AS uuid), CAST(:nid AS uuid), "
            "CAST(:pid AS uuid), CAST(:u AS uuid), :path, 'audio/wav', 'transcribed', "
            ":tr, 'redraft', now(), now())"
        ),
        {
            "sid": session_id,
            "nid": note_id,
            "pid": patient_id,
            "u": _AVERY,
            "path": f"dictations/{session_id}/seeded",
            "tr": _X_DICTATION,
        },
    )


@pytest.fixture
def outside(tmp_path: Path) -> _Outside:
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    return _Outside(bucket=bucket)


@pytest.fixture
def sessions(engine: Engine, tenant_schema: str, seed: _Seed, outside: _Outside) -> _Sessions:
    """Two sessions in each state on Xavier's chart, written as Avery.

    The signed notes are signed through the note service and the real
    repository, so their signed versions are the ones the routes write. The
    finished recordings already have both audio channels in storage, where
    the signed-URL finalize step looks for them.
    """
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.services.note_service import NoteService  # noqa: PLC0415

    s = _Sessions(*(str(uuid.uuid4()) for _ in range(6)))
    db, tokens = _open_session(engine, tenant_schema, _AVERY)
    try:
        for session_id in (s.recorded_twin, s.recorded):
            _insert_session(db, session_id, seed.xavier, _AVERY, "recording_complete")
            for channel in ("therapist", "client"):
                obj = outside.bucket / "signed" / session_id / f"{channel}.pcm"
                obj.parent.mkdir(parents=True, exist_ok=True)
                obj.write_bytes(b"\x00" * 64)
        for session_id in (s.review_twin, s.review):
            note_id = str(uuid.uuid4())
            _insert_session(db, session_id, seed.xavier, _AVERY, "pending_review")
            _insert_note(db, note_id, session_id, seed.xavier, _AVERY, _X_BODY)
            _insert_dictation(db, session_id, note_id, seed.xavier)
        service = NoteService(PostgresNotesRepository(db))
        for session_id in (s.signed_twin, s.signed):
            note_id = str(uuid.uuid4())
            _insert_session(db, session_id, seed.xavier, _AVERY, "finalized")
            _insert_note(db, note_id, session_id, seed.xavier, _AVERY, _X_BODY)
            service.sign_note(
                note_id, signer_name="Avery Ames", signer_credentials="LCSW", user_id=_AVERY
            )
        db.commit()
    finally:
        _close_session(db, tokens)
    return s


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


class _NeverTranscribes:
    """A dictation transcriber the routes can see; the queued job never runs here."""

    def transcribe(self, audio: bytes, content_type: str) -> str:
        del audio, content_type
        raise AssertionError("dictation jobs are queued, not run, in these tests")


def _wire_outside(monkeypatch: pytest.MonkeyPatch, outside: _Outside) -> None:
    """Transcription on, storage a recorded local directory, the queue a list."""
    from app.settings import get_settings  # noqa: PLC0415

    settings = get_settings().model_copy(
        update={
            "transcription_enabled": True,
            "transcription_provider": "assemblyai",
            "transcription_audio_bucket": str(outside.bucket),
            # No in-process drafting or transcribing after the response.
            "note_generation_base_url": "",
            "dictation_transcription_base_url": "",
        }
    )
    for module in ("app.routes.sessions", "app.routes.session_dictations", "app.routes.notes"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    def _storage(_settings: object) -> _RecordingStorage:
        return outside.storage

    for module in (
        "app.services.file_storage",
        "app.routes.session_dictations",
        "app.routes.notes",
    ):
        monkeypatch.setattr(f"{module}.file_storage_from_settings", _storage)
    monkeypatch.setattr(
        "app.routes.session_dictations.get_dictation_transcriber",
        lambda _settings: _NeverTranscribes(),
    )

    def _enqueue(queue: str, path: str, payload: dict[str, Any], **_kw: object) -> None:
        outside.jobs.append((queue, path, payload))

    for module in ("app.routes.sessions", "app.routes.session_dictations"):
        monkeypatch.setattr(f"{module}.enqueue", _enqueue)


@pytest.fixture
def client(  # noqa: PLR0913 — fixture composition mirrors the FastAPI deps
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    outside: _Outside,
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
    from app.routes.sessions import get_note_generation_service  # noqa: PLC0415
    from app.services.note_generation_service import MockNoteGenerationService  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    del seed
    _truncate_audit(engine, tenant_schema)
    _wire_outside(monkeypatch, outside)

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
        # wrong clinician would pass or fail for the wrong reason. Armed on
        # the session, so it survives the routes' mid-request commits.
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
    # Never a model call; the drafting job is queued, not run, anyway.
    fastapi_app.dependency_overrides[get_note_generation_service] = (
        lambda: MockNoteGenerationService()  # noqa: PLW0108
    )

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


def _rows(conn: Any, table: str, where: str, params: dict[str, Any]) -> list[str]:
    """Each row whole, as JSON text, so any changed column shows."""
    # table and where are literals from this module, never user input.
    sql = f"SELECT to_jsonb(t)::text AS r FROM {table} t {where} ORDER BY t.id"  # noqa: S608
    return [r for (r,) in conn.execute(text(sql), params)]


def _footprint(engine: Engine, schema: str, seed: _Seed, outside: _Outside) -> dict[str, Any]:
    """Every row on Xavier's chart these routes touch, the audit trail, and the outside.

    The chart is read as Avery, who holds its grant and so sees every row on
    it whoever wrote them. The audit trail is read under the purge GUC, the
    one setting that sees every actor's rows (``audit_logs`` is FORCE RLS).
    """
    on_chart = "WHERE t.patient_id = CAST(:pid AS uuid)"
    pid = {"pid": seed.xavier}
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _AVERY})
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        footprint = {
            table: _rows(conn, table, on_chart, pid)
            for table in (
                "therapy_sessions",
                "notes",
                "note_signatures",
                "note_addenda",
                "session_dictations",
            )
        }
        footprint["audit_logs"] = _rows(conn, "audit_logs", "", {})
        footprint["patient"] = _rows(conn, "patients", "WHERE t.id = CAST(:pid AS uuid)", pid)
        conn.rollback()
    footprint.update(outside.snapshot())
    return footprint


def _assert_nothing_of_xaviers(resp: Response) -> None:
    for marker in (_X_LAST, _X_TRANSCRIPT, _X_BODY, _X_DICTATION, "storage.invalid"):
        assert marker not in resp.text, marker


# ---------------------------------------------------------------------------
# Routes that take Avery's session or client by id
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Route:
    name: str
    method: str
    path: Callable[[str, _Seed], str]
    #: Which of Avery's sessions the route acts on: a finished recording, a
    #: draft in review, a signed note, or none (the route takes the client).
    state: str
    ok: int
    body: Callable[[_Seed], dict[str, Any]] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None
    form: dict[str, str] | None = None


def _session(suffix: str = "") -> Callable[[str, _Seed], str]:
    return lambda session_id, _s: f"/api/sessions/{session_id}{suffix}"


def _client_upload(_session_id: str, s: _Seed) -> str:
    return f"/api/patients/{s.xavier}/sessions/upload"


def _new_session_body(s: _Seed) -> dict[str, Any]:
    return {
        "patient_id": s.xavier,
        "session_date": datetime.now(UTC).isoformat(),
        "transcript": {"format": "txt", "content": _B_TEXT},
    }


_TWO_CHANNELS = {
    "therapist_audio": ("therapist.wav", _WAV, "audio/wav"),
    "client_audio": ("client.wav", _WAV, "audio/wav"),
}

_ROUTES = (
    _Route("detail", "GET", _session(), "review", 200),
    _Route(
        "transcript",
        "POST",
        _session("/transcript"),
        "recorded",
        202,
        body=lambda _s: {"format": "txt", "content": _B_TEXT},
    ),
    _Route("upload_audio", "POST", _session("/upload-audio"), "recorded", 202, files=_TWO_CHANNELS),
    _Route("upload_audio_init", "POST", _session("/upload-audio/init"), "recorded", 201),
    _Route("upload_audio_finalize", "POST", _session("/upload-audio/finalize"), "recorded", 202),
    _Route(
        "finalize",
        "PATCH",
        _session("/finalize"),
        "review",
        200,
        body=lambda _s: {
            "quality_rating": 1,
            "soap_note_edited": {
                "subjective": _B_TEXT,
                "objective": "",
                "assessment": "",
                "plan": "",
            },
        },
    ),
    _Route("client_session_upload", "POST", _client_upload, "none", 202, body=_new_session_body),
    _Route("dictations_list", "GET", _session("/dictations"), "review", 200),
    _Route(
        "dictation_add",
        "POST",
        _session("/dictations"),
        "signed",
        202,
        files={"audio": ("more.wav", _WAV, "audio/wav")},
        form={"duration_seconds": "12"},
    ),
)


def _targets(route: _Route, s: _Sessions) -> tuple[str, str]:
    """(Avery's control session, Blake's target session) for ``route``."""
    if route.state == "recorded":
        return s.recorded_twin, s.recorded
    if route.state == "signed":
        return s.signed_twin, s.signed
    return s.review_twin, s.review


def _send(client: TestClient, route: _Route, path: str, seed: _Seed) -> Response:
    if route.files is not None:
        multipart: Response = client.request(route.method, path, files=route.files, data=route.form)
        return multipart
    body = route.body(seed) if route.body is not None else None
    resp: Response = client.request(route.method, path, json=body)
    return resp


def _assert_control(route: _Route, resp: Response, outside: _Outside) -> None:
    """Avery's call did what the route is for, so Blake's 404 is not vacuous."""
    assert resp.status_code == route.ok, (route.name, resp.status_code, resp.text[:500])
    body = resp.json()
    if route.name == "detail":
        assert _X_LAST in resp.text
        assert _X_BODY in resp.text
    elif route.name == "transcript":
        assert body["status"] == "processing"
        assert outside.jobs[-1][1] == "/api/internal/jobs/generate-soap"
    elif route.name in {"upload_audio", "upload_audio_finalize"}:
        assert body["status"] == "transcribing"
        assert outside.jobs[-1][1] == "/api/internal/assemblyai-submit"
    elif route.name == "upload_audio_init":
        assert body["therapist"]["upload"]["url"].startswith("https://storage.invalid/")
        assert len(outside.storage.minted) == 2
    elif route.name == "finalize":
        assert body["status"] == "finalized"
        assert body["note"]["content_edited"]["subjective"] == _B_TEXT
    elif route.name == "client_session_upload":
        assert body["status"] == "processing"
        assert outside.jobs[-1][1] == "/api/internal/jobs/generate-soap"
    elif route.name == "dictations_list":
        assert [d["transcript"] for d in body["data"]] == [_X_DICTATION]
    elif route.name == "dictation_add":
        assert body["status"] == "transcribing"
        assert outside.jobs[-1][1] == "/api/internal/jobs/session-dictation"


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blake_cannot_reach_averys_session(  # noqa: PLR0913 — fixtures
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    sessions: _Sessions,
    outside: _Outside,
) -> None:
    twin, target = _targets(route, sessions)

    # Control: the route works for the clinician who holds the grant, on a
    # session in the same state as the one Blake is about to try.
    control = _send(client, route, route.path(twin, seed), seed)
    _assert_control(route, control, outside)

    before = _footprint(engine, tenant_schema, seed, outside)
    # Not vacuous: Avery's request was audited, so one by Blake would show.
    assert any(f'"user_id": "{_AVERY}"' in r for r in before["audit_logs"])

    _as(caller, _BLAKE)
    denied = _send(client, route, route.path(target, seed), seed)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied)
    # Nothing written: same sessions, notes, signed versions, addenda and
    # dictations, no audit row by Blake, no stored object, no upload URL,
    # no queued job.
    assert _footprint(engine, tenant_schema, seed, outside) == before


# ---------------------------------------------------------------------------
# Blake's own client: Blake is a working clinician, armed as themselves
# ---------------------------------------------------------------------------


def test_control_blake_reads_their_own_clients_session(
    client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    _as(caller, _BLAKE)

    detail = client.get(f"/api/sessions/{seed.y_session}")
    dictations = client.get(f"/api/sessions/{seed.y_session}/dictations")

    assert detail.status_code == 200, detail.text[:500]
    assert _Y_LAST in detail.text
    assert _Y_BODY in detail.text
    assert dictations.status_code == 200, dictations.text[:500]
    assert dictations.json()["data"] == []


# ---------------------------------------------------------------------------
# Ids that name nothing: Blake's 404 is a stranger's 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blakes_404_is_the_404_for_an_id_that_names_nothing(
    route: _Route, client: TestClient, caller: _Caller, seed: _Seed, sessions: _Sessions
) -> None:
    nobody = _Seed(xavier=str(uuid.uuid4()))
    made_up_session = str(uuid.uuid4())
    _, target = _targets(route, sessions)
    _as(caller, _BLAKE)

    theirs = _send(client, route, route.path(target, seed), seed)
    absent = _send(client, route, route.path(made_up_session, nobody), nobody)

    assert (theirs.status_code, absent.status_code) == (404, 404)
    raw = json.dumps(absent.json())
    for made_up, real in ((nobody.xavier, seed.xavier), (made_up_session, target)):
        raw = raw.replace(made_up, real)
    # Some 404s name the id they were asked for; the ids differ, and nothing else may.
    assert theirs.json() == json.loads(raw)
