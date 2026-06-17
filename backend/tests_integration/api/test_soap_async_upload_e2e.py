# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end: async ``POST /sessions/upload`` (persist-and-202) + the
internal SOAP-generation worker, proven against a real Postgres + RLS with a
**mock LLM** (no Gemini calls).

This is the TDD safety net for the upload_session async slice (THERAPY-jonc.1).
What it de-risks is *not* generation quality — that's why the LLM is mocked —
but the two things that have no working reference anywhere in the tree and are
HIPAA-grade if wrong:

  1. **The persist-202 lifecycle.** ``upload`` must persist the session in
     ``PROCESSING`` and return ``202 {session_id}`` *without* running the LLM
     on the request thread; the worker must later flip it to ``PENDING_REVIEW``
     (or ``FAILED``) and attach the note.
  2. **Off-request tenant scoping in the worker.** The worker is delivered by
     Cloud Tasks authenticated as a service account — it does NOT inherit the
     uploader's tenant session. It must resolve the payload ``user_id`` to its
     tenant schema, arm ``search_path`` + the RLS ``app.current_user_id`` GUC,
     and write the note into the *correct* schema. Get the resolution wrong and
     a note flushes into another tenant's schema. The unit suite (MagicMock'd
     engine) cannot see this class of bug; only a real schema + RLS can.

Mirrors ``test_patients_api_e2e.py``: real Postgres via the parent
``conftest.py`` testcontainer, ``alembic upgrade head``, and tenant schemas
provisioned through the production ``create_practice_schema`` path (RLS on).

The cross-tenant assertion is the load-bearing one: after the worker runs for
tenant A's user, tenant **B**'s schema must contain zero sessions and zero
notes.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
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

# ENVIRONMENT must be set before any ``app.*`` import (settings is lru_cached).
os.environ.setdefault("ENVIRONMENT", "development")
# Multi-tenancy on so the worker actually has to resolve + set a schema rather
# than short-circuiting to DEFAULT_PRACTICE_SCHEMA — the whole point here.
os.environ.setdefault("MULTI_TENANCY_ENABLED", "true")

_USER_A = "9d1c4b2a-7e63-4f10-9a2b-1c0d5e6f7a80"
_USER_B = "2f8e6d5c-4b3a-4291-8c7d-6e5f4a3b2c10"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _provision_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # The RLS policies reference ``has_patient_access`` unqualified (it lives in
    # ``practice``), so warm the pool with a search_path that includes it before
    # the policy CREATE — same prerequisite as the patients e2e fixture.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_async_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


@pytest.fixture(scope="module")
def tenant_a_schema(engine: Engine) -> Iterator[str]:
    schema = _provision_schema(engine, "a")
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def tenant_b_schema(engine: Engine) -> Iterator[str]:
    schema = _provision_schema(engine, "b")
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def fastapi_app() -> FastAPI:
    from app.main import app  # noqa: PLC0415

    return app


def _user(user_id: str, email: str):
    from app.models import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=email,
        name="Async E2E User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def tenant_a_client(
    fastapi_app: FastAPI,
    tenant_a_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A request-side client scoped to tenant A's user, with the LLM mocked.

    Auth + schema resolution are overridden to tenant A; the note-generation
    service is swapped for the deterministic ``MockNoteGenerationService`` so no
    Gemini call is ever made. This client is for the *uploader* side; the worker
    is invoked separately (it must scope itself, not inherit this).
    """
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import get_db_session  # noqa: PLC0415
    from app.routes.sessions import get_note_generation_service  # noqa: PLC0415
    from app.services.note_generation_service import MockNoteGenerationService  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user_a = _user(_USER_A, "async-a@example.com")

    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request",
        lambda _request: tenant_a_schema,
    )

    def _tenant_context() -> TenantContext:
        session = get_db_session()
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": _USER_A},
        )
        return TenantContext(
            user_id=_USER_A, practice_id="tenant-a", practice_schema=tenant_a_schema
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: _USER_A
    fastapi_app.dependency_overrides[get_current_user] = lambda: user_a
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: user_a
    fastapi_app.dependency_overrides[require_active_subscription] = lambda: user_a
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: user_a
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_note_generation_service] = MockNoteGenerationService

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _seed_patient(client: TestClient) -> str:
    resp = client.post("/api/patients", json={"first_name": "Ada", "last_name": "Lovelace"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _count(engine: Engine, schema: str, table: str) -> int:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"), {"uid": _USER_A}
        )
        # table is a controlled literal from this module, never user input.
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def _session_status(engine: Engine, schema: str, session_id: str) -> str | None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"), {"uid": _USER_A}
        )
        row = conn.execute(
            text("SELECT status FROM therapy_sessions WHERE id = CAST(:id AS uuid)"),
            {"id": session_id},
        ).scalar_one_or_none()
        return row


_TRANSCRIPT = {"format": "txt", "content": "[00:00] Therapist: Hi.\n[00:01] Client: Hello."}


class TestAsyncUploadPersistAnd202:
    """``upload`` returns 202 immediately and persists a PROCESSING session —
    no LLM on the request thread."""

    def test_upload_returns_202_with_processing_session_and_no_note(
        self, tenant_a_client: TestClient, engine: Engine, tenant_a_schema: str
    ) -> None:
        patient_id = _seed_patient(tenant_a_client)

        resp = tenant_a_client.post(
            f"/api/patients/{patient_id}/sessions/upload",
            json={
                "patient_id": patient_id,
                "session_date": datetime.now(UTC).isoformat(),
                "transcript": _TRANSCRIPT,
            },
        )

        assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        session_id = body["id"]
        # The note is generated by the worker, not on this request.
        assert body["note"] is None, "upload must not block on generation"
        assert _session_status(engine, tenant_a_schema, session_id) == "processing"


class TestSoapWorkerTenantScoping:
    """The worker generates the note into the *correct* tenant schema and
    nowhere else — the load-bearing cross-tenant proof."""

    def test_worker_writes_note_into_tenant_a_only(  # noqa: PLR0913 — mirrors FastAPI deps
        self,
        tenant_a_client: TestClient,
        fastapi_app: FastAPI,
        engine: Engine,
        tenant_a_schema: str,
        tenant_b_schema: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient  # noqa: PLC0415

        patient_id = _seed_patient(tenant_a_client)
        upload = tenant_a_client.post(
            f"/api/patients/{patient_id}/sessions/upload",
            json={
                "patient_id": patient_id,
                "session_date": datetime.now(UTC).isoformat(),
                "transcript": _TRANSCRIPT,
            },
        )
        assert upload.status_code == 202, upload.text
        session_id = upload.json()["id"]

        notes_in_b_before = _count(engine, tenant_b_schema, "notes")

        # Invoke the worker the way Cloud Tasks will: SA-authed (override the
        # invoker gate), schema resolved from user_id (not a request token). The
        # worker must scope itself — it does NOT see tenant_a_client's overrides.
        from app.auth.service import require_cloud_tasks_invoker  # noqa: PLC0415

        monkeypatch.setattr(
            "app.services.session_generation_worker.resolve_tenant_schema_for_user",
            lambda user_id: tenant_a_schema if user_id == _USER_A else None,
        )
        fastapi_app.dependency_overrides[require_cloud_tasks_invoker] = lambda: None
        try:
            worker = TestClient(fastapi_app).post(
                "/api/internal/jobs/generate-soap",
                json={"session_id": session_id, "user_id": _USER_A},
            )
        finally:
            fastapi_app.dependency_overrides.pop(require_cloud_tasks_invoker, None)

        assert worker.status_code == 200, f"worker failed: {worker.status_code} {worker.text}"

        # Tenant A: session completed and the note landed.
        assert _session_status(engine, tenant_a_schema, session_id) == "pending_review"
        assert _count(engine, tenant_a_schema, "notes") >= 1
        # Tenant B: untouched. This is the cross-tenant-write guard.
        assert _count(engine, tenant_b_schema, "notes") == notes_in_b_before
        assert _count(engine, tenant_b_schema, "therapy_sessions") == 0


class TestSoapWorkerFailurePath:
    """A generation failure marks the session FAILED and writes no partial
    note — the status is durable, not lost to a rollback."""

    def test_generation_error_marks_session_failed(
        self,
        tenant_a_client: TestClient,
        fastapi_app: FastAPI,
        engine: Engine,
        tenant_a_schema: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.auth.service import require_cloud_tasks_invoker  # noqa: PLC0415
        from app.routes.sessions import get_note_generation_service  # noqa: PLC0415
        from app.services.note_generation_service import NoteGenerationService  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        patient_id = _seed_patient(tenant_a_client)
        upload = tenant_a_client.post(
            f"/api/patients/{patient_id}/sessions/upload",
            json={
                "patient_id": patient_id,
                "session_date": datetime.now(UTC).isoformat(),
                "transcript": _TRANSCRIPT,
            },
        )
        session_id = upload.json()["id"]

        class _ExplodingGen(NoteGenerationService):
            def generate_note(self, *_args, **_kwargs):  # type: ignore[override]
                raise RuntimeError("simulated generation failure")

        monkeypatch.setattr(
            "app.services.session_generation_worker.resolve_tenant_schema_for_user",
            lambda _user_id: tenant_a_schema,
        )
        fastapi_app.dependency_overrides[require_cloud_tasks_invoker] = lambda: None
        fastapi_app.dependency_overrides[get_note_generation_service] = _ExplodingGen
        try:
            worker = TestClient(fastapi_app).post(
                "/api/internal/jobs/generate-soap",
                json={"session_id": session_id, "user_id": _USER_A},
            )
        finally:
            fastapi_app.dependency_overrides.pop(require_cloud_tasks_invoker, None)
            fastapi_app.dependency_overrides.pop(get_note_generation_service, None)

        # The worker should not 500 the queue into infinite retries on a
        # deterministic generation failure; it records FAILED and returns.
        assert _session_status(engine, tenant_a_schema, session_id) == "failed"
        assert _count(engine, tenant_a_schema, "notes") == 0
        assert worker.status_code in (200, 422)
