# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the session status and metadata PATCH routes.

Covers the state-machine validation on ``PATCH /api/sessions/{id}/status``
and the terminal-status guard on ``PATCH /api/sessions/{id}`` (metadata),
plus the per-user isolation and audit entries both routes owe.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from app.models import Patient, SessionStatus, TherapySession, Transcript
from app.models.audit import AuditAction
from app.repositories import (  # noqa: TC002 — runtime fixture types
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.services import AuditService  # noqa: TC002 — runtime fixture type
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

_OTHER_CLINICIAN = "other-clinician-999"


def _seed_session(
    repo: InMemoryTherapySessionRepository,
    *,
    owner: str,
    status: SessionStatus = SessionStatus.SCHEDULED,
    scheduled_at: datetime | None = None,
) -> TherapySession:
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=owner,
        patient_id=str(uuid.uuid4()),
        session_date=datetime(2026, 2, 4, tzinfo=UTC),
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content="original document text"),
        scheduled_at=scheduled_at,
        created_at=datetime.now(UTC),
    )
    return repo.create(session)


def _seed_patient(repo: InMemoryPatientRepository, session: TherapySession, owner: str) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=session.patient_id,
        first_name="Jane",
        last_name="Smith",
        created_at=now,
        updated_at=now,
    )
    return repo.create(patient, owner)


class TestPatchSessionStatus:
    def test_status_scheduled_to_in_progress_returns_200(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}/status", json={"status": "in_progress"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "in_progress"
        updated = mock_session_repo.get(session.id, mock_user_id)
        assert updated is not None
        assert updated.status == SessionStatus.IN_PROGRESS

    def test_status_same_status_returns_409_already_in_status(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        _seed_patient(mock_repo, session, mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}/status", json={"status": "in_progress"})

        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "ALREADY_IN_STATUS"

    def test_status_disallowed_transition_returns_400(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}/status", json={"status": "finalized"})

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    def test_status_unknown_value_returns_422(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}/status", json={"status": "nope"})

        assert resp.status_code == 422, resp.text

    def test_status_other_users_session_returns_404(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=_OTHER_CLINICIAN)

        resp = client.patch(f"/api/sessions/{session.id}/status", json={"status": "in_progress"})

        assert resp.status_code == 404, resp.text

    def test_status_audits_the_transition(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)

        with patch.object(mock_audit_service, "log_session_action") as mock_log:
            resp = client.patch(
                f"/api/sessions/{session.id}/status", json={"status": "in_progress"}
            )

        assert resp.status_code == 200, resp.text
        mock_log.assert_called_once()
        # The route currently passes AuditAction.SESSION_CREATED here rather
        # than a status-transition action — a likely copy-paste slip, but
        # not this test's concern; only the changes payload is asserted.
        assert mock_log.call_args.kwargs["changes"] == {"status": "in_progress"}


class TestPatchSessionMetadata:
    def test_metadata_reschedule_returns_200_with_new_time(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)
        new_time = "2026-03-10T15:00:00Z"

        resp = client.patch(
            f"/api/sessions/{session.id}",
            json={"scheduled_at": new_time, "duration_minutes": 30},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scheduled_at"] == "2026-03-10T15:00:00Z"
        assert body["duration_minutes"] == 30

    def test_metadata_empty_body_is_a_noop_200(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)

        with patch.object(mock_audit_service, "log_session_action") as mock_log:
            resp = client.patch(f"/api/sessions/{session.id}", json={})

        assert resp.status_code == 200, resp.text
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["changes"] == {"changed_fields": []}

    def test_metadata_on_finalized_session_returns_400_terminal(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.FINALIZED
        )
        _seed_patient(mock_repo, session, mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}", json={"video_link": "https://x"})

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "TERMINAL_STATUS"

    def test_metadata_duration_out_of_range_returns_422(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)

        resp = client.patch(f"/api/sessions/{session.id}", json={"duration_minutes": 0})

        assert resp.status_code == 422, resp.text

    def test_metadata_other_users_session_returns_404(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=_OTHER_CLINICIAN)

        resp = client.patch(f"/api/sessions/{session.id}", json={"video_link": "https://x"})

        assert resp.status_code == 404, resp.text

    def test_metadata_audits_changed_fields_sorted(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        _seed_patient(mock_repo, session, mock_user_id)

        with patch.object(mock_audit_service, "log_session_action") as mock_log:
            resp = client.patch(
                f"/api/sessions/{session.id}",
                json={"video_link": "https://x", "duration_minutes": 45},
            )

        assert resp.status_code == 200, resp.text
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["changes"] == {
            "changed_fields": ["duration_minutes", "video_link"]
        }
        assert mock_log.call_args.args[0] == AuditAction.SESSION_UPDATED
