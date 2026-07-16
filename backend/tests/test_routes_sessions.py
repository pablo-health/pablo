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

from app.models import SessionSource, SessionStatus, TherapySession, Transcript
from app.models.audit import AuditAction
from app.repositories import InMemoryTherapySessionRepository  # noqa: TC002 — runtime fixture type
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
