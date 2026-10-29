# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``GET /api/patients/{patient_id}/activity``: who may read it, and what leaves.

Which rows the owner can read is the row policy's job, and is proven against
Postgres in ``tests_integration/database/test_audit_owner_client_read_rls.py``.
These cover the route's own promises: the owner gate, that nothing but the
action leaves (no ``changes``, no IP, no user agent), how each actor is named,
and that reading the trail writes to it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.db.platform_models import PracticeRow
from app.models import Patient, User
from app.models.audit import (
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_PLATFORM_STAFF,
    ACTOR_TYPE_SYSTEM,
    AuditAction,
    AuditLogEntry,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_activity

if TYPE_CHECKING:
    from app.repositories import InMemoryPatientRepository, InMemoryUserRepository
    from app.services import AuditService
    from fastapi.testclient import TestClient

COLLEAGUE_ID = "colleague-1"


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


@pytest.fixture
def audit_repo(mock_audit_service: AuditService) -> InMemoryAuditRepository:
    repo = InMemoryAuditRepository()
    mock_audit_service._repo = repo
    return repo


@pytest.fixture
def patient(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> Patient:
    now = datetime.now(UTC)
    seeded = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    return mock_repo.create(seeded, mock_user_id)


def _owned_by(monkeypatch: pytest.MonkeyPatch, owner_id: str | None) -> None:
    """The practice row the route reads, recording ``owner_id`` as its owner."""
    row = PracticeRow(id="test-practice", schema_name="practice_test", owner_user_id=owner_id)
    monkeypatch.setattr(patient_activity, "_practice", lambda _practice_id: row)


def _row(patient_id: str, minutes_ago: int, **fields: Any) -> AuditLogEntry:
    return AuditLogEntry(
        patient_id=patient_id,
        resource_type="patient",
        resource_id=patient_id,
        timestamp=_iso(datetime.now(UTC) - timedelta(minutes=minutes_ago)),
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0 Firefox/130.0",
        changes={"changed_fields": ["email"]},
        **fields,
    )


class TestOnlyTheOwnerReadsIt:
    def test_a_clinician_who_is_not_the_owner_is_refused(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, COLLEAGUE_ID)
        audit_repo.append(_row(patient.id, 1, user_id=COLLEAGUE_ID, action="patient_viewed"))

        resp = client.get(f"/api/patients/{patient.id}/activity")

        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "NOT_PRACTICE_OWNER"
        # Refused before anything is read, so nothing is recorded as read.
        assert not [r for r in audit_repo.all() if r.action == "patient_activity_viewed"]

    def test_a_practice_with_no_recorded_owner_has_no_reader(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, patient: Patient
    ) -> None:
        _owned_by(monkeypatch, None)

        assert client.get(f"/api/patients/{patient.id}/activity").status_code == 403

    def test_the_owner_without_access_to_the_client_gets_not_found(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)

        resp = client.get(f"/api/patients/{uuid.uuid4()}/activity")

        assert resp.status_code == 404


class TestWhatLeavesTheRoute:
    def test_actions_only_never_content_or_request_details(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)
        audit_repo.append(
            _row(
                patient.id,
                1,
                user_id=patient.id,
                actor_type=ACTOR_TYPE_PATIENT,
                action="patient_message_sent",
            )
        )

        body = client.get(f"/api/patients/{patient.id}/activity").json()

        item = body["data"][0]
        assert set(item) == {
            "id",
            "timestamp",
            "actor_type",
            "actor_name",
            "is_you",
            "action",
            "resource_type",
        }
        flat = str(body)
        assert "203.0.113.7" not in flat
        assert "Firefox" not in flat
        assert "changed_fields" not in flat
        # Nor the actor's raw id: for a client's own row that is the client's id.
        assert "user_id" not in item

    def test_each_actor_is_named_as_the_reader_would_say_it(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        mock_user_repo: InMemoryUserRepository,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)
        mock_user_repo.update(
            User(
                id=COLLEAGUE_ID,
                email="c@example.com",
                name="Riley Colleague",
                created_at=datetime.now(UTC),
            )
        )
        rows = [
            (ACTOR_TYPE_CLINICIAN, mock_user_id, None),
            (ACTOR_TYPE_CLINICIAN, COLLEAGUE_ID, None),
            (ACTOR_TYPE_PATIENT, patient.id, None),
            (ACTOR_TYPE_SYSTEM, mock_user_id, "appointment_reminders"),
            (ACTOR_TYPE_PLATFORM_STAFF, "staff-unknown", None),
            (ACTOR_TYPE_ANONYMOUS, mock_user_id, None),
        ]
        for minutes, (actor_type, user_id, component) in enumerate(rows):
            audit_repo.append(
                _row(
                    patient.id,
                    minutes,
                    user_id=user_id,
                    actor_type=actor_type,
                    actor_component=component,
                    action="patient_viewed",
                )
            )

        data = client.get(f"/api/patients/{patient.id}/activity").json()["data"]

        assert [(d["actor_type"], d["actor_name"], d["is_you"]) for d in data] == [
            ("clinician", None, True),
            ("clinician", "Riley Colleague", False),
            ("patient", None, False),
            ("system", "appointment_reminders", False),
            ("staff", None, False),
            # The public booking row is scoped to the owner but was not theirs.
            ("anonymous", None, False),
        ]


class TestReadingIsRecorded:
    def test_viewing_writes_patient_activity_viewed_after_the_read(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)
        audit_repo.append(_row(patient.id, 5, user_id=COLLEAGUE_ID, action="patient_viewed"))

        body = client.get(f"/api/patients/{patient.id}/activity").json()

        viewed = [r for r in audit_repo.all() if r.action == AuditAction.PATIENT_ACTIVITY_VIEWED]
        assert len(viewed) == 1
        assert viewed[0].user_id == mock_user_id
        assert viewed[0].patient_id == patient.id
        assert viewed[0].changes == {"returned_count": 1}
        # Written after the page was read, so it is not on the page.
        assert [d["action"] for d in body["data"]] == ["patient_viewed"]

    def test_a_full_page_hands_back_a_cursor_that_reaches_older_rows(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)
        for minutes in range(1, 4):
            audit_repo.append(
                _row(patient.id, minutes, user_id=COLLEAGUE_ID, action=f"action_{minutes}")
            )

        first = client.get(f"/api/patients/{patient.id}/activity?limit=2").json()
        second = client.get(
            f"/api/patients/{patient.id}/activity?limit=2&cursor={first['next_cursor']}"
        ).json()

        assert [d["action"] for d in first["data"]] == ["action_1", "action_2"]
        assert [d["action"] for d in second["data"]] == ["action_3"]
        assert second["next_cursor"] is None

    def test_a_malformed_cursor_is_a_bad_request(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_user_id: str,
        patient: Patient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _owned_by(monkeypatch, mock_user_id)

        resp = client.get(f"/api/patients/{patient.id}/activity?cursor=not-a-cursor")

        assert resp.status_code == 400
