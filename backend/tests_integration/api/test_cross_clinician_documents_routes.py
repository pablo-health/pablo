# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Documents, one clinician against another clinician's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and uploaded the documents on his chart. Blake holds a grant on
Yolanda, with a document of her own, and none on Xavier, so Blake is a working
clinician throughout and a 404 below is never a broken account. Every request
runs through the real app, the real repositories and the real
``has_patient_access``, against a practice schema provisioned the production
way, on a connection subject to row security. The request's principal is armed
as whoever is calling.

Stood in: object storage (an in-memory provider, so an upload can be started
and finished and a download URL minted) and the job queue (a recorder, so a
finalize that queued text extraction would show).

Three kinds of case:

* **Every clinician document route on Avery's client or document** (list,
  start an upload, read, the download URL in both dispositions, finish an
  upload, delete). Avery calls each one first and gets a 2xx; then Blake
  calls it on Avery's object and gets 404, never 403 or 409, with nothing of
  Xavier's in the body. After Blake's attempt every document row, the audit
  trail and the job queue are exactly as Avery left them.
* **A grant that expires.** Blake is given a grant on Xavier that ends in an
  hour: the list, the read and the download URL all answer. The grant's
  ``expires_at`` moves into the past, and the same requests are a 404 with no
  audit row and no URL. A URL minted before the expiry cannot be taken back;
  what this proves is that no new one is minted.
* **Lookups by an id that names nothing**, so a 404 for Blake is the same 404
  a stranger gets.

The download URL is ``GET /api/documents/{id}/file``: it answers with the
signed URL as JSON rather than the file, so "file" and "download URL" are one
route, run here in both of its dispositions.

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
    from app.repositories.patient_document import PatientDocumentRepository
    from app.services.patient_documents_service import PatientDocumentsService
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

_TENANT_EMAIL = "e2e-cross-clinician-documents@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000d1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000d2"
_BUCKET = "cross-clinician-documents-test-bucket"

# Distinctive enough that a substring match in a body is proof. Letters only:
# a sentinel made of digits would turn up by chance in ids and timestamps.
_X_FILE = "Quillfeatherx-referral.pdf"
_X_TEXT = "Quillfeatherx extracted letter"
_Y_FILE = "Marrowgatey-referral.pdf"

# Enough of a PDF for the storage stand-in to report a real size and type.
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Avery's finished document on Xavier: read, listed, downloaded.
    x_doc: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Avery's two unfinished uploads on Xavier: one Avery finishes as the
    # control, one Blake tries to finish.
    x_pending_control: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_pending: str = field(default_factory=lambda: str(uuid.uuid4()))
    # A second finished document of Avery's, which Avery deletes as the control.
    x_spare: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_doc: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class _Caller:
    """Who the next request is from. Every auth override reads this."""

    user: User


@dataclass
class _Queue:
    """The jobs a request asked for, in order."""

    jobs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


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


@pytest.fixture
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client and the documents on each chart, fresh per test.

    Fresh ids every time, so no test sees another's charts and none needs
    cleaning up.

    Each clinician's rows are written as that clinician, so every row passes
    the same policy a request would have to.
    """
    s = _Seed()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    charts = (
        (
            _AVERY,
            s.xavier,
            "Xavier",
            (
                (s.x_doc, _X_FILE, _X_TEXT, at),
                (s.x_spare, "spare.pdf", "spare", at),
                (s.x_pending_control, "pending-control.pdf", None, None),
                (s.x_pending, "pending.pdf", None, None),
            ),
        ),
        (_BLAKE, s.yolanda, "Yolanda", ((s.y_doc, _Y_FILE, "hers", at),)),
    )
    for clinician, patient, first, documents in charts:
        with engine.begin() as conn:
            _scoped(conn, tenant_schema, clinician)
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :fn, 'Doe', :fnl, 'doe', 'active', 0, "
                    "now(), now())"
                ),
                {"pid": patient, "fn": first, "fnl": first.lower()},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient, "u": clinician},
            )
            for doc_id, name, extracted, finalized_at in documents:
                conn.execute(
                    text(
                        "INSERT INTO patient_documents (id, patient_id, user_id, filename, "
                        "mime_type, gcs_path, extracted_text, size_bytes, category, "
                        "extraction_status, created_at, finalized_at) "
                        "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:u AS uuid), "
                        ":name, 'application/pdf', :gcs, :txt, :size, 'chart', "
                        ":status, :at, :fin)"
                    ),
                    {
                        "id": doc_id,
                        "pid": patient,
                        "u": clinician,
                        "name": name,
                        "gcs": f"patients/{patient}/{doc_id}.pdf",
                        "txt": extracted,
                        "size": len(_PDF),
                        "status": "complete" if finalized_at else "pending",
                        "at": at,
                        "fin": finalized_at,
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


def _memory_storage() -> Any:
    """Object storage that always holds a small, genuine PDF.

    Not under test: every route below decides access from the database before
    storage is asked anything. It exists so a control can start and finish an
    upload and mint a download URL.
    """
    from app.services.file_storage import FileStorageProvider, UploadTarget

    class _MemoryStorage(FileStorageProvider):
        def make_upload_target(self, **_kw: Any) -> UploadTarget:
            return UploadTarget(url="https://storage.example.test/upload", method="PUT")

        def make_download_url(self, *, object_name: str, **_kw: Any) -> str:  # type: ignore[override]
            return f"https://storage.example.test/{object_name}"

        def fetch_metadata(self, **_kw: Any) -> tuple[int, str | None]:
            return len(_PDF), "application/pdf"

        def download_bytes(self, **_kw: Any) -> bytes:
            return _PDF

        def upload_bytes(self, **_kw: Any) -> None:
            return None

        def upload_stream(self, **_kw: Any) -> None:
            return None

        def delete(self, **_kw: Any) -> None:
            return None

        def list_names(self, **_kw: Any) -> list[str]:
            return []

    return _MemoryStorage()


@pytest.fixture
def caller() -> _Caller:
    return _Caller(user=_user(_AVERY))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> _Queue:
    q = _Queue()

    def _enqueue(_queue: str, path: str, payload: dict[str, Any], **_kw: Any) -> None:
        q.jobs.append((path, payload))

    monkeypatch.setattr("app.services.patient_documents_service.enqueue", _enqueue)
    return q


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    queue: _Queue,
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
    from app.routes.patient_documents import (
        get_patient_document_repository,
        get_patient_documents_service,
    )
    from app.services.patient_documents_service import (
        PatientDocumentsService,
    )
    from app.settings import get_settings
    from fastapi import Depends
    from fastapi.testclient import TestClient

    del seed, queue  # requested for their setup only
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
        # keep the arming, or a control would read nothing for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return caller.user

    storage = _memory_storage()
    settings = get_settings().model_copy(update={"patient_documents_gcs_bucket": _BUCKET})

    def _service(
        repo: PatientDocumentRepository = Depends(get_patient_document_repository),
    ) -> PatientDocumentsService:
        # The real service on the real repository; only the storage it signs
        # against is a stand-in, and no OCR client.
        return PatientDocumentsService(
            repo=repo, settings=settings, storage=storage, tenant_id="test-tenant"
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: caller.user.id
    fastapi_app.dependency_overrides[get_current_user] = _user_now
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _user_now
    fastapi_app.dependency_overrides[require_active_subscription] = _user_now
    fastapi_app.dependency_overrides[require_baa_acceptance] = _user_now
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_patient_documents_service] = _service

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


def _documents(engine: Engine, schema: str) -> dict[str, list[str]]:
    """Every document row as text, read under each clinician's arm.

    A chart row on Xavier is Avery's to see and a restricted one Blake wrote
    would be Blake's, so between the two arms any row either of them could
    have written or changed is in here, whole: ``finalized_at``,
    ``extraction_status``, ``size_bytes`` and ``deleted_at`` included.
    """
    seen: dict[str, list[str]] = {}
    for who in (_AVERY, _BLAKE):
        with engine.connect() as conn:
            _scoped(conn, schema, who)
            seen[who] = [
                r[0]
                for r in conn.execute(text("SELECT d::text FROM patient_documents d ORDER BY d.id"))
            ]
            conn.rollback()
    return seen


def _footprint(engine: Engine, schema: str, queue: _Queue) -> dict[str, Any]:
    return {
        "documents": _documents(engine, schema),
        "audit": _audit(engine, schema),
        "jobs": list(queue.jobs),
    }


def _assert_nothing_of_xaviers(resp: Response) -> None:
    """No filename, no text, no URL. A 404 may echo the id it was asked for."""
    for marker in (_X_FILE, _X_TEXT, "storage.example.test"):
        assert marker not in resp.text, marker


# ---------------------------------------------------------------------------
# Every route, Blake against Avery's client or document
# ---------------------------------------------------------------------------


_INIT_BODY = {"filename": "intruder.pdf", "mime_type": "application/pdf", "size_bytes": 2048}


@dataclass(frozen=True)
class _Route:
    name: str
    method: str
    # The path Blake is refused on.
    path: Callable[[_Seed], str]
    # The path Avery's control runs on, where the route consumes its object.
    control_path: Callable[[_Seed], str] | None = None
    body: dict[str, Any] | None = None
    # What Avery's control must carry, so Blake's 404 is not vacuous.
    shows: Callable[[_Seed], str] = lambda s: s.x_doc


_ROUTES = (
    _Route("list", "GET", lambda s: f"/api/patients/{s.xavier}/documents"),
    _Route(
        "init",
        "POST",
        lambda s: f"/api/patients/{s.xavier}/documents/init",
        body=_INIT_BODY,
        shows=lambda _s: "storage.example.test",
    ),
    _Route("read", "GET", lambda s: f"/api/documents/{s.x_doc}"),
    _Route(
        "download-url",
        "GET",
        lambda s: f"/api/documents/{s.x_doc}/file",
        shows=lambda s: f"storage.example.test/patients/{s.xavier}/{s.x_doc}.pdf",
    ),
    _Route(
        "download-url-inline",
        "GET",
        lambda s: f"/api/documents/{s.x_doc}/file?disposition=inline",
        shows=lambda s: f"storage.example.test/patients/{s.xavier}/{s.x_doc}.pdf",
    ),
    _Route(
        "finalize",
        "POST",
        lambda s: f"/api/documents/{s.x_pending}/finalize",
        control_path=lambda s: f"/api/documents/{s.x_pending_control}/finalize",
        shows=lambda s: s.x_pending_control,
    ),
    _Route(
        "delete",
        "DELETE",
        lambda s: f"/api/documents/{s.x_doc}",
        control_path=lambda s: f"/api/documents/{s.x_spare}",
        shows=lambda _s: "Document deleted",
    ),
)


def _send(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> Response:
    resp: Response = client.request(method, path, json=body)
    return resp


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blake_cannot_reach_averys_document(
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    queue: _Queue,
) -> None:
    control_path = (route.control_path or route.path)(seed)

    # Control: the route serves the clinician who holds the grant.
    control = _send(client, route.method, control_path, route.body)
    assert 200 <= control.status_code < 300, (route.name, control.status_code, control.text[:500])
    assert route.shows(seed) in control.text, (route.name, control.text[:500])
    # Not vacuous: Avery's call was audited, so one by Blake would show too.
    assert any(r["user_id"] == _AVERY for r in _audit(engine, tenant_schema))

    before = _footprint(engine, tenant_schema, queue)
    _as(caller, _BLAKE)
    denied = _send(client, route.method, route.path(seed), route.body)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied)
    # Nothing written: no row created, finished or tombstoned, no audit row,
    # no extraction queued.
    assert _footprint(engine, tenant_schema, queue) == before


def test_avery_can_still_finish_the_upload_blake_was_refused(
    client: TestClient, caller: _Caller, seed: _Seed, queue: _Queue
) -> None:
    """Blake's 404 on finalize was about who asked, not about the upload."""
    _as(caller, _BLAKE)
    assert client.post(f"/api/documents/{seed.x_pending}/finalize").status_code == 404
    assert queue.jobs == []

    _as(caller, _AVERY)
    done = client.post(f"/api/documents/{seed.x_pending}/finalize")

    assert done.status_code == 202, done.text[:500]
    assert done.json()["extraction_status"] == "pending"
    assert queue.jobs == [
        (
            "/api/internal/jobs/finalize-document",
            {"document_id": seed.x_pending, "user_id": _AVERY},
        )
    ]


def test_blake_still_reaches_their_own_client(
    client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    """Blake is a working clinician: the same routes answer on Yolanda's chart."""
    _as(caller, _BLAKE)

    listed = client.get(f"/api/patients/{seed.yolanda}/documents")
    read = client.get(f"/api/documents/{seed.y_doc}")
    url = client.get(f"/api/documents/{seed.y_doc}/file")

    assert listed.status_code == 200, listed.text[:500]
    assert [d["id"] for d in listed.json()["data"]] == [seed.y_doc]
    assert read.status_code == 200, read.text[:500]
    assert read.json()["filename"] == _Y_FILE
    assert url.status_code == 200, url.text[:500]
    assert seed.y_doc in url.json()["url"]


# ---------------------------------------------------------------------------
# A grant that expires
# ---------------------------------------------------------------------------


_EXPIRY_ROUTES = (
    _Route("list", "GET", lambda s: f"/api/patients/{s.xavier}/documents"),
    _Route("read", "GET", lambda s: f"/api/documents/{s.x_doc}"),
    _Route(
        "download-url",
        "GET",
        lambda s: f"/api/documents/{s.x_doc}/file",
        shows=lambda s: f"storage.example.test/patients/{s.xavier}/{s.x_doc}.pdf",
    ),
    _Route(
        "download-url-inline",
        "GET",
        lambda s: f"/api/documents/{s.x_doc}/file?disposition=inline",
        shows=lambda s: f"storage.example.test/patients/{s.xavier}/{s.x_doc}.pdf",
    ),
)


def _set_blakes_grant(engine: Engine, schema: str, seed: _Seed, expires_at: datetime) -> None:
    """Give Blake a grant on Xavier ending at ``expires_at``, or move its end there.

    Written as Blake: a clinician's grants are the rows their own arm sees.
    """
    with engine.begin() as conn:
        _scoped(conn, schema, _BLAKE)
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by, "
                "expires_at) VALUES (CAST(:p AS uuid), :b, 'co_treating', :a, :exp) "
                "ON CONFLICT (patient_id, user_id) DO UPDATE SET expires_at = EXCLUDED.expires_at"
            ),
            {"p": seed.xavier, "b": _BLAKE, "a": _AVERY, "exp": expires_at},
        )


@pytest.mark.parametrize("route", _EXPIRY_ROUTES, ids=lambda r: r.name)
def test_blakes_grant_stops_reaching_the_document_once_it_expires(
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    queue: _Queue,
) -> None:
    now = datetime.now(UTC)
    _set_blakes_grant(engine, tenant_schema, seed, now + timedelta(hours=1))
    _as(caller, _BLAKE)

    # Control: inside the window, the grant reaches Avery's document.
    inside = client.get(route.path(seed))
    assert inside.status_code == 200, (route.name, inside.text[:500])
    assert route.shows(seed) in inside.text, (route.name, inside.text[:500])
    assert any(r["user_id"] == _BLAKE for r in _audit(engine, tenant_schema))

    _set_blakes_grant(engine, tenant_schema, seed, now - timedelta(minutes=1))
    before = _footprint(engine, tenant_schema, queue)

    after = client.get(route.path(seed))

    assert after.status_code == 404, (route.name, after.status_code, after.text[:500])
    _assert_nothing_of_xaviers(after)
    # No new URL minted and no disclosure recorded.
    assert _footprint(engine, tenant_schema, queue) == before

    # Avery's own grant is untouched by Blake's ending.
    _as(caller, _AVERY)
    assert client.get(route.path(seed)).status_code == 200


# ---------------------------------------------------------------------------
# Ids that name nothing: Blake's 404 is a stranger's 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blakes_404_is_the_404_for_an_id_that_names_nothing(
    route: _Route, client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    nobody = _Seed()
    _as(caller, _BLAKE)

    theirs = _send(client, route.method, route.path(seed), route.body)
    absent = _send(client, route.method, route.path(nobody), route.body)

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
        (nobody.x_doc, seed.x_doc),
        (nobody.x_pending, seed.x_pending),
    ):
        raw = raw.replace(made_up, real)
    return json.loads(raw)
