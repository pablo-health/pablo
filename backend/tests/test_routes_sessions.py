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

from app.models import SessionSource, SessionStatus, TherapySession, Transcript
from app.repositories import InMemoryTherapySessionRepository  # noqa: TC002 — runtime fixture type
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

# A clinician who is NOT the test's authenticated user (conftest's mock_user).
_OTHER_CLINICIAN = "other-clinician-999"


def _seed_session(
    repo: InMemoryTherapySessionRepository,
    *,
    owner: str,
    status: SessionStatus = SessionStatus.PENDING_REVIEW,
    source: str | None = None,
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
