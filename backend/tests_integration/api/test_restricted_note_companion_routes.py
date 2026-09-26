# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The companion's read routes never serve a psychotherapy note.

The companion reaches the record through sessions: it lists and opens
sessions, reads the note embedded on each one, and drives an external EHR
through the ``ehr_routes`` endpoints. A psychotherapy note is standalone —
it is written with no ``session_id`` — so none of those paths has a way to
reach it. That is true by construction; this module makes it true by test.

One clinician, one patient, one session, two notes:

* a SOAP note on the session, which the session routes must embed;
* a psychotherapy note on the same patient by the same clinician, with no
  session, which none of the routes below may return by id or by body.

**The absence is not vacuous.** The clinician here is the note's author, so
row security lets them read it — ``GET /api/notes/{id}`` returns it, and the
first test proves that before any route is asked to leave it out. Every
session route also has to show the SOAP note it does embed, so a route that
returned nothing at all could not pass.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
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

    from app.models.ehr_route import GoalNavigationRequest, GoalNavigationResponse
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
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

_TENANT_EMAIL = "e2e-restricted-note-routes@example.com"
_CLINICIAN = "3e8b1c47-5a2d-5f90-b6e4-7c1d9a2f0b58"
# Distinctive enough that a substring match in a response body is proof.
_RESTRICTED_BODY = "psychotherapy-note-body-that-must-stay-with-its-author"
_SOAP_BODY = "progress-note-subjective-the-session-routes-embed"


class _Seed:
    def __init__(self) -> None:
        self.patient_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.soap_note_id = str(uuid.uuid4())
        self.restricted_note_id = str(uuid.uuid4())


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


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


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """The patient, the session, both notes and a cached EHR route.

    Written as the clinician, so every row passes the same policy a request
    would have to.
    """
    s = _Seed()
    now = datetime.now(UTC).replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', "
                "'ada', 'lovelace', 'active', 1, :now, :now)"
            ),
            {"pid": s.patient_id, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": s.patient_id, "u": _CLINICIAN},
        )
        # Scheduled now, so ``/api/sessions/today`` returns it too.
        conn.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                "session_number, status, transcript, created_at, updated_at, "
                "scheduled_at, duration_minutes, session_type, source) "
                "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), :now, "
                "1, 'pending_review', CAST(:tr AS jsonb), :now, :now, "
                ":now, 50, 'individual', 'companion')"
            ),
            {
                "id": s.session_id,
                "u": _CLINICIAN,
                "pid": s.patient_id,
                "tr": json.dumps({"format": "txt", "content": "Transcript."}),
                "now": now,
            },
        )
        _insert_note(
            conn,
            note_id=s.soap_note_id,
            patient_id=s.patient_id,
            session_id=s.session_id,
            note_type="soap",
            content={"subjective": _SOAP_BODY, "objective": "", "assessment": "", "plan": ""},
            restricted=False,
            now=now,
        )
        _insert_note(
            conn,
            note_id=s.restricted_note_id,
            patient_id=s.patient_id,
            session_id=None,
            note_type="psychotherapy",
            content={"note": {"body": _RESTRICTED_BODY}},
            restricted=True,
            now=now,
        )
        conn.execute(
            text(
                "INSERT INTO ehr_routes (id, ehr_system, route_name, steps, "
                "success_count, created_at, updated_at) "
                "VALUES ('simplepractice', 'simplepractice', 'To the note form', "
                "CAST(:steps AS jsonb), 0, :now, :now)"
            ),
            {
                "steps": json.dumps(
                    [
                        {
                            "action": "click",
                            "selector": "#clients",
                            "a11y_fingerprint": "button:Clients",
                            "intent": "open the client list",
                        }
                    ]
                ),
                "now": now,
            },
        )
    return s


def _insert_note(  # noqa: PLR0913 — one keyword per column the proof depends on
    conn: Connection,
    *,
    note_id: str,
    patient_id: str,
    session_id: str | None,
    note_type: str,
    content: dict[str, object],
    restricted: bool,
    now: datetime,
) -> None:
    conn.execute(
        text(
            "INSERT INTO notes (id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:sid AS uuid), :nt, "
            "CAST(:c AS jsonb), 'complete', CAST(:author AS uuid), :restricted, :now, :now)"
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "sid": session_id,
            "nt": note_type,
            "c": json.dumps(content),
            "author": _CLINICIAN,
            "restricted": restricted,
            "now": now,
        },
    )


@pytest.fixture(scope="module")
def fastapi_app() -> FastAPI:
    from app.main import app  # noqa: PLC0415 — connects to the database at import

    return app


class _FixedNavigation:
    """Stands in for the LLM behind ``/api/ehr-navigate``; reads nothing."""

    async def navigate(self, _request: GoalNavigationRequest) -> GoalNavigationResponse:
        from app.models.ehr_route import GoalNavigationResponse  # noqa: PLC0415

        return GoalNavigationResponse(
            action="click",
            selector="#notes",
            reasoning="The note form is one click away.",
            confidence=0.9,
            is_on_target_page=False,
        )


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    tenant_schema: str,
    seed: _Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The real route stack, repositories and row policies, as the clinician.

    Wired as ``test_patients_api_e2e.e2e_client``: the middleware resolves
    the practice from a stashed identity, and the auth overrides arm
    ``app.current_user_id`` on the request session the way production does.
    The only other substitution is the LLM behind ``/api/ehr-navigate``.
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
    from app.routes.ehr_routes import get_ehr_navigation_service  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user = User(
        id=_CLINICIAN,
        email=_TENANT_EMAIL,
        name="Restricted Note Routes",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
    identity = VerifiedIdentity(
        provider="test", subject_id=_CLINICIAN, email=_TENANT_EMAIL, mfa_satisfied=True, claims={}
    )

    def _stash_identity(request: Request) -> None:
        request.state.verified_identity = identity

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    # Production arms the row-security principal in the user dependency
    # itself (``auth.service._resolve_user``), so a route that never asks for
    # the tenant context, like ``/api/ehr-navigate``, still writes its audit
    # row as the clinician. The overrides keep that.
    def _armed_user() -> User:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return user

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return TenantContext(
            user_id=_CLINICIAN,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: _CLINICIAN
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_ehr_navigation_service] = _FixedNavigation

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _assert_restricted_note_absent(body: str, seed: _Seed) -> None:
    assert seed.restricted_note_id not in body
    assert _RESTRICTED_BODY not in body


def test_control_the_clinician_can_read_the_restricted_note(
    client: TestClient, seed: _Seed
) -> None:
    """Without this, every absence below could be row security hiding it."""
    response = client.get(f"/api/notes/{seed.restricted_note_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restricted"] is True
    assert body["session_id"] is None
    assert _RESTRICTED_BODY in response.text


@pytest.mark.parametrize(
    "path",
    ["/api/sessions", "/api/sessions/today", "/api/sessions/{session_id}"],
)
def test_session_reads_embed_the_soap_note_and_not_the_restricted_one(
    client: TestClient, seed: _Seed, path: str
) -> None:
    response = client.get(path.format(session_id=seed.session_id))
    assert response.status_code == 200, response.text
    assert seed.session_id in response.text, "control: the route returned the session"
    if path != "/api/sessions/today":
        # The today view carries no note; the other two embed it.
        assert seed.soap_note_id in response.text, "control: the SOAP note is embedded"
        assert _SOAP_BODY in response.text
    _assert_restricted_note_absent(response.text, seed)


def test_ehr_route_read_returns_navigation_only(client: TestClient, seed: _Seed) -> None:
    response = client.get("/api/ehr-routes/simplepractice")
    assert response.status_code == 200, response.text
    assert response.json()["route_name"] == "To the note form"
    _assert_restricted_note_absent(response.text, seed)


def test_ehr_route_step_update_returns_navigation_only(client: TestClient, seed: _Seed) -> None:
    response = client.patch(
        "/api/ehr-routes/simplepractice/steps/0",
        json={"selector": "#clients-v2", "a11y_fingerprint": "button:Clients"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["steps"][0]["selector"] == "#clients-v2"
    _assert_restricted_note_absent(response.text, seed)


def test_ehr_navigate_returns_navigation_only(client: TestClient, seed: _Seed) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "simplepractice",
            "goal": "Open the progress note form for this session",
            "current_url": "https://secure.simplepractice.com/clients",
            "dom_snapshot": "<main><button>Notes</button></main>",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["selector"] == "#notes"
    _assert_restricted_note_absent(response.text, seed)
