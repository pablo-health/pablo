# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's homework routes over HTTP, on the real stack and row policies.

One clinician, one client, one signed SOAP note with a homework list. The
routes are driven the way the note and the chart drive them: save the
clinician's edit to the plan (which suggests its homework), find the
suggestions in the note's chart-update review, accept one edited and dismiss
the other there, then add one by hand, reword it and withdraw it.

What each test would catch: a saved edit that suggests no homework (the
step gated to chart-reading types), suggestions that never reach the
chart-update review, or an accepted one that never becomes an assignment; a
second decision that overwrites the first
instead of answering 409; a client the clinician holds no grant on answering
anything but 404; and an audit row that carries the task's words, which are
PHI, instead of ids.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

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

_TENANT_EMAIL = "homework-routes@example.com"
_CLINICIAN = "6b2e9d14-0c7a-5f3b-a8e1-4d9c2f7b1a60"
# Distinctive, so its absence from the audit log is proof rather than luck.
_TASK = "Complete two thought records about the elevator"


class _Seed:
    def __init__(self) -> None:
        self.patient_id = str(uuid.uuid4())
        self.stranger_patient_id = str(uuid.uuid4())
        self.note_id = str(uuid.uuid4())
        self.content: dict[str, object] = {}


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice provisioned the production way, mapped to ``_TENANT_EMAIL``."""
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


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    s = _Seed()
    now = datetime.now(UTC).replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        for pid in (s.patient_id, s.stranger_patient_id):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), 'Pat', 'Anonymous', 'pat', 'anonymous', "
                    "'active', 0, :now, :now)"
                ),
                {"pid": pid, "now": now},
            )
        # A grant on the first client only: the second is someone else's.
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": s.patient_id, "u": _CLINICIAN},
        )
        s.content = content = {
            "plan": {
                "homework_assignments": [
                    {"text": f"Client will {_TASK[0].lower()}{_TASK[1:]}."},
                    {"text": "Practice paced breathing."},
                ]
            }
        }
        conn.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, content, finalized_at, status, "
                "author_user_id, restricted, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'soap', CAST(:c AS jsonb), "
                "NULL, 'complete', CAST(:u AS uuid), false, :now, :now)"
            ),
            {
                "id": s.note_id,
                "pid": s.patient_id,
                "c": json.dumps(content),
                "u": _CLINICIAN,
                "now": now,
            },
        )
    return s


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    tenant_schema: str,
    seed: _Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The real route stack as the clinician, wired as the restricted-note route suite is."""
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
    from app.notes import get_default_registry, register_builtin_note_types  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    register_builtin_note_types(get_default_registry())
    user = User(
        id=_CLINICIAN,
        email=_TENANT_EMAIL,
        name="Homework Routes",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
    identity = VerifiedIdentity(
        provider="test",
        subject_id=_CLINICIAN,
        email=_TENANT_EMAIL,
        email_verified=True,
        mfa_satisfied=True,
        claims={},
    )

    def _stash_identity(request: Request) -> None:
        request.state.verified_identity = identity

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _armed_user() -> User:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return user

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return TenantContext(
            user_id=_CLINICIAN, practice_id="test-tenant", practice_schema=tenant_schema
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: _CLINICIAN
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _audit(engine: Engine, schema: str) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        rows = conn.execute(
            text(
                "SELECT action, coalesce(changes::text, '') FROM audit_logs ORDER BY \"timestamp\""
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


def test_the_note_suggests_homework_in_the_chart_updates_and_accepting_assigns_it(
    client: TestClient, seed: _Seed, engine: Engine, tenant_schema: str
) -> None:
    base = f"/api/patients/{seed.patient_id}/homework"

    opened = client.get(base)
    assert opened.status_code == 200, opened.text
    assert opened.json() == {"assignments": []}

    # Saving the clinician's edit is what suggests the plan's homework.
    saved = client.patch(f"/api/notes/{seed.note_id}", json={"content_edited": seed.content})
    assert saved.status_code == 200, saved.text

    # The suggestions are in the note's chart-update review, with the note's words.
    review = client.get(f"/api/notes/{seed.note_id}/chart-proposals")
    assert review.status_code == 200, review.text
    suggestions = sorted(review.json()["data"], key=lambda p: p["item_key"])
    assert [(p["field_key"], p["label"], p["proposed_text"]) for p in suggestions] == [
        ("homework", "Homework: Assignment", f"{_TASK}."),
        ("homework", "Homework: Assignment", "Practice paced breathing."),
    ]
    assert suggestions[0]["what_changed"].startswith("In the plan: “Client will")
    assert suggestions[0]["editable"] is True

    decide = f"/api/notes/{seed.note_id}/chart-proposals"
    first, second = suggestions
    edited = client.post(
        f"{decide}/{first['id']}/decision", json={"decision": "edit", "text": _TASK}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["decision"] == "edited"
    dismissed = client.post(f"{decide}/{second['id']}/decision", json={"decision": "discard"})
    assert dismissed.status_code == 200, dismissed.text
    again = client.post(f"{decide}/{second['id']}/decision", json={"decision": "accept"})
    assert again.status_code == 409, "the first answer stands"

    (assigned,) = client.get(base).json()["assignments"]
    assert assigned["status"] == "assigned"
    assert assigned["text"] == _TASK
    assert assigned["proposed_text"] == f"{_TASK}."
    assert assigned["origin"] == "note"
    assert assigned["note_id"] == seed.note_id

    added = client.post(base, json={"text": "  Walk after dinner  "})
    assert added.status_code == 201, added.text
    assert added.json()["text"] == "Walk after dinner"
    assert added.json()["origin"] == "clinician"
    reworded = client.patch(
        f"{base}/{added.json()['id']}", json={"text": "Walk 15 minutes after dinner"}
    )
    assert reworded.status_code == 200, reworded.text
    withdrawn = client.post(f"{base}/{added.json()['id']}/withdraw")
    assert withdrawn.json()["status"] == "withdrawn"
    assert client.post(f"{base}/{added.json()['id']}/withdraw").status_code == 409
    assert client.patch(f"{base}/{added.json()['id']}", json={"text": "x"}).status_code == 409

    audit = _audit(engine, tenant_schema)
    actions = [a for a, _ in audit]
    for action in ("homework_added", "homework_edited", "homework_withdrawn"):
        assert action in actions, action
    assert any('"field_key": "homework"' in changes for _, changes in audit), (
        "accepting a suggestion is audited where chart updates are decided"
    )
    assert all("elevator" not in changes and "dinner" not in changes for _, changes in audit), (
        "audit rows carry ids, never the task's words"
    )


def test_a_client_without_a_grant_is_not_found(client: TestClient, seed: _Seed) -> None:
    base = f"/api/patients/{seed.stranger_patient_id}/homework"
    assert client.get(base).status_code == 404
    assert client.post(base, json={"text": "Read"}).status_code == 404


def test_blank_text_is_refused(client: TestClient, seed: _Seed) -> None:
    base = f"/api/patients/{seed.patient_id}/homework"
    assert client.post(base, json={"text": "   "}).status_code == 422
