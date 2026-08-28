# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Access-control (IDOR) tests for the session routes.

Fills the per-route gap the import endpoint surfaced: a session — recorded,
uploaded, or imported — must be invisible to anyone who isn't the owning
clinician (or someone granted access to the patient). Cross-tenant isolation
is structural (schema separation); these cover the intra-tenant, per-user
access check on the session routes (GET / list / finalize), enforced by
``session_repo.get(id, user_id)`` and ``list_by_user``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.main import app
from app.models import Patient, SessionSource, SessionStatus, TherapySession, Transcript
from app.models.audit import AuditAction
from app.notes import NoteTypeAuthorizer, get_note_type_authorizer
from app.repositories import (  # noqa: TC002 — runtime fixture types
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.routes.sessions import _is_final_soap_attempt
from app.services import AuditService  # noqa: TC002 — runtime fixture type
from fastapi import HTTPException, status
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

# A clinician who is NOT the test's authenticated user (conftest's mock_user).
_OTHER_CLINICIAN = "other-clinician-999"


def _seed_session(
    repo: InMemoryTherapySessionRepository,
    *,
    owner: str,
    status: SessionStatus = SessionStatus.PENDING_REVIEW,
    source: str | None = None,
    scheduled_at: datetime | None = None,
) -> TherapySession:
    """Create a session owned by ``owner``. create() grants access to the
    owner only — no other user can reach it."""
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=owner,
        patient_id=str(uuid.uuid4()),
        session_date=datetime(2026, 2, 4, tzinfo=UTC),
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content="original document text"),
        source=source,
        scheduled_at=scheduled_at,
        created_at=datetime.now(UTC),
    )
    return repo.create(session)


class TestSessionRouteAccessControl:
    def test_owner_can_get_own_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        # Positive control: the owner sees their own session, so a 404 below is
        # real isolation rather than a trivially-broken route.
        session = _seed_session(mock_session_repo, owner=mock_user_id)
        resp = client.get(f"/api/sessions/{session.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == session.id

    def test_get_returns_404_for_another_users_imported_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        # An imported note owned by another clinician must not leak via GET.
        session = _seed_session(
            mock_session_repo,
            owner=_OTHER_CLINICIAN,
            source=SessionSource.IMPORTED.value,
        )
        resp = client.get(f"/api/sessions/{session.id}")
        assert resp.status_code == 404

    def test_finalize_returns_404_for_another_users_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _seed_session(mock_session_repo, owner=_OTHER_CLINICIAN)
        resp = client.patch(f"/api/sessions/{session.id}/finalize", json={})
        assert resp.status_code == 404

    def test_list_excludes_other_users_sessions(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        mine = _seed_session(mock_session_repo, owner=mock_user_id)
        other = _seed_session(
            mock_session_repo,
            owner=_OTHER_CLINICIAN,
            source=SessionSource.IMPORTED.value,
        )
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        ids = {s["id"] for s in resp.json()["data"]}
        assert mine.id in ids
        assert other.id not in ids


class TestSessionListAudit:
    """The session list embeds full SOAP content per item, so it must audit a
    per-record ``session_viewed`` for each session it returns — the same
    audit-of-record as the detail view, kept affordable by read-coalescing.
    """

    def test_list_emits_session_viewed_per_returned_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        first = _seed_session(mock_session_repo, owner=mock_user_id)
        second = _seed_session(mock_session_repo, owner=mock_user_id)

        resp = client.get("/api/sessions")
        assert resp.status_code == 200

        viewed = {
            call.args[0].resource_id
            for call in mock_audit_service._repo.append.call_args_list
            if call.args[0].action == AuditAction.SESSION_VIEWED.value
        }
        assert viewed == {first.id, second.id}


class TestTodaySessionsAudit:
    """GET /api/sessions/today discloses patient names + free-text session notes,
    so it audits a per-record ``session_viewed`` for each session it returns —
    the same audit-of-record contract as the full session list.
    """

    def test_today_emits_session_viewed_per_returned_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        now = datetime.now(UTC)
        first = _seed_session(mock_session_repo, owner=mock_user_id, scheduled_at=now)
        second = _seed_session(mock_session_repo, owner=mock_user_id, scheduled_at=now)

        resp = client.get("/api/sessions/today")
        assert resp.status_code == 200

        viewed = {
            call.args[0].resource_id
            for call in mock_audit_service._repo.append.call_args_list
            if call.args[0].action == AuditAction.SESSION_VIEWED.value
        }
        assert viewed == {first.id, second.id}


class TestPatchSessionStatus:
    """PATCH /api/sessions/{id}/status — the in_progress -> scheduled revert
    a therapist needs when a recording never actually started."""

    def test_patch_status_scheduled_reverts_an_in_progress_session(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_audit_service: AuditService,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.IN_PROGRESS
        )
        now = datetime.now(UTC)
        patient = Patient(
            id=session.patient_id,
            first_name="Jane",
            last_name="Smith",
            created_at=now,
            updated_at=now,
        )
        mock_repo.create(patient, mock_user_id)

        resp = client.patch(
            f"/api/sessions/{session.id}/status",
            json={"status": "scheduled"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "scheduled"
        assert body["started_at"] is None

        changes = {
            call.args[0].resource_id: call.args[0].changes
            for call in mock_audit_service._repo.append.call_args_list
        }
        assert changes[session.id] == {"status": "scheduled"}


class TestUploadTranscriptAsync:
    """POST /api/sessions/{id}/transcript persists the transcript and returns
    202, handing generation to the worker rather than generating inline
    (THERAPY-jonc)."""

    def test_returns_202_and_marks_processing(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.RECORDING_COMPLETE
        )

        resp = client.post(
            f"/api/sessions/{session.id}/transcript",
            json={"format": "txt", "content": "[00:00] Hello."},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["id"] == session.id
        assert body["status"] == SessionStatus.PROCESSING.value
        # Persisted as PROCESSING with the new transcript; the note is produced
        # off-request by the worker, so none is generated inline here.
        updated = mock_session_repo.get(session.id, mock_user_id)
        assert updated is not None
        assert updated.status == SessionStatus.PROCESSING
        assert updated.transcript.content == "[00:00] Hello."

    def test_rejects_session_in_wrong_status(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.PENDING_REVIEW
        )

        resp = client.post(
            f"/api/sessions/{session.id}/transcript",
            json={"format": "txt", "content": "x"},
        )

        assert resp.status_code == 400, resp.text


class TestUploadAudioRateLimit:
    def test_rate_limit_exceeded_returns_429(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_user_id: str,
    ) -> None:
        """A caller over the per-user burst limit gets 429 before transcription."""
        session = _seed_session(
            mock_session_repo, owner=mock_user_id, status=SessionStatus.RECORDING_COMPLETE
        )

        def raise_429(key: str) -> None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )

        with patch("app.routes.sessions.get_audio_upload_limiter") as mock_limiter:
            mock_limiter.return_value.check.side_effect = raise_429
            resp = client.post(
                f"/api/sessions/{session.id}/upload-audio",
                files={
                    "therapist_audio": ("t.wav", b"RIFFdata", "audio/wav"),
                    "client_audio": ("c.wav", b"RIFFdata", "audio/wav"),
                },
            )

        assert resp.status_code == 429


class TestIsFinalSoapAttempt:
    """The Cloud Tasks retry-count gate that decides whether a transient SOAP
    failure gets one more retry or is recorded as terminal."""

    @staticmethod
    def _request(retry_count: str | None) -> object:
        headers = {} if retry_count is None else {"X-CloudTasks-TaskRetryCount": retry_count}
        return SimpleNamespace(headers=headers)

    def test_early_attempts_are_not_final(self) -> None:
        # Default soap_generation_max_attempts is 5 → final at retry_count >= 4.
        assert not _is_final_soap_attempt(self._request("0"))
        assert not _is_final_soap_attempt(self._request("3"))

    def test_last_attempt_is_final(self) -> None:
        assert _is_final_soap_attempt(self._request("4"))
        assert _is_final_soap_attempt(self._request("5"))

    def test_missing_or_garbage_header_defaults_to_first_attempt(self) -> None:
        assert not _is_final_soap_attempt(self._request(None))
        assert not _is_final_soap_attempt(self._request("not-a-number"))


class _DenyAllNoteTypes(NoteTypeAuthorizer):
    """Deployment-style authorizer that locks every note type."""

    def is_allowed(self, user: object, note_type: str) -> bool:
        return False


class TestScheduleSession:
    """POST /api/sessions/schedule — note_type plumbing at the route layer.

    The service-level wiring (default, persistence, unknown-type rejection)
    is covered in test_start_session_from_appointment.TestNoteTypeWiring;
    these pin the HTTP contract: the 400 error shape for an unknown type
    and the authorizer 403 for a type the caller's deployment has locked
    (the same gate /api/appointments/{id}/start-session applies).
    """

    @staticmethod
    def _seed_patient(repo: InMemoryPatientRepository, user_id: str) -> Patient:
        now = datetime.now(UTC)
        patient = Patient(
            id=str(uuid.uuid4()),
            first_name="Jane",
            last_name="Smith",
            created_at=now,
            updated_at=now,
        )
        return repo.create(patient, user_id)

    @staticmethod
    def _payload(patient_id: str, **extra: object) -> dict[str, object]:
        return {
            "patient_id": patient_id,
            "scheduled_at": "2026-02-04T10:00:00Z",
            **extra,
        }

    def test_schedule_with_note_type_pre_creates_note(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = self._seed_patient(mock_repo, mock_user_id)

        resp = client.post(
            "/api/sessions/schedule",
            json=self._payload(patient.id, note_type="narrative"),
        )

        assert resp.status_code == 201, resp.text
        note = mock_notes_repo.get_by_session_id(resp.json()["id"])
        assert note is not None
        assert note.note_type == "narrative"

    def test_schedule_without_note_type_defaults_to_soap(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = self._seed_patient(mock_repo, mock_user_id)

        resp = client.post("/api/sessions/schedule", json=self._payload(patient.id))

        assert resp.status_code == 201, resp.text
        note = mock_notes_repo.get_by_session_id(resp.json()["id"])
        assert note is not None
        assert note.note_type == "soap"

    def test_schedule_unknown_note_type_returns_400(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = self._seed_patient(mock_repo, mock_user_id)

        resp = client.post(
            "/api/sessions/schedule",
            json=self._payload(patient.id, note_type="not-a-real-type"),
        )

        assert resp.status_code == 400, resp.text
        assert "INVALID_NOTE_TYPE" in resp.text

    def test_schedule_locked_note_type_returns_403(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """A deployment-injected authorizer that locks the requested type
        must reject at the route (403), mirroring the appointment
        start-session gate — before any session or note is created."""
        patient = self._seed_patient(mock_repo, mock_user_id)
        app.dependency_overrides[get_note_type_authorizer] = _DenyAllNoteTypes
        try:
            resp = client.post(
                "/api/sessions/schedule",
                json=self._payload(patient.id, note_type="narrative"),
            )
        finally:
            app.dependency_overrides.pop(get_note_type_authorizer, None)

        assert resp.status_code == 403, resp.text

    def test_schedule_omitted_note_type_bypasses_authorizer(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """No explicit note_type → the default applies without an authorizer
        check (parity with the appointment route's rule that falling back
        to the default is always allowed)."""
        patient = self._seed_patient(mock_repo, mock_user_id)
        app.dependency_overrides[get_note_type_authorizer] = _DenyAllNoteTypes
        try:
            resp = client.post("/api/sessions/schedule", json=self._payload(patient.id))
        finally:
            app.dependency_overrides.pop(get_note_type_authorizer, None)

        assert resp.status_code == 201, resp.text
