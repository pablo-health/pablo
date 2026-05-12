# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/notes (pa-0nx.2 + pa-0nx.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.main import app
from app.models import Note, Patient, Transcript
from app.notes import NoteTypeAuthorizer, get_note_type_authorizer
from app.repositories import (
    InMemoryNotesRepository,
    InMemoryPatientRepository,
)
from app.routes.notes import (
    get_note_generation_service,
)
from app.routes.notes import (
    get_notes_repository as get_notes_route_notes_repository,
)
from app.routes.sessions import (
    get_notes_repository as get_sessions_notes_repository,
)
from app.services import GeneratedNote, NoteGenerationService
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

_SOAP: dict[str, Any] = {
    "subjective": "S",
    "objective": "O",
    "assessment": "A",
    "plan": "P",
}


def _seed_note(notes_repo: InMemoryNotesRepository, *, finalized: bool = False) -> Note:
    now = datetime.now(UTC)
    note = Note(
        id=str(uuid.uuid4()),
        patient_id="patient-1",
        session_id=str(uuid.uuid4()),
        note_type="soap",
        content=_SOAP,
        created_at=now,
        updated_at=now,
    )
    if finalized:
        note.finalized_at = now
        note.quality_rating = 4
    return notes_repo.add(note)


class TestGetNote:
    def test_returns_note_when_found(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo)
        response = client.get(f"/api/notes/{note.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == note.id
        assert body["session_id"] == note.session_id
        assert body["content"] == _SOAP

    def test_returns_404_when_missing(self, client: TestClient) -> None:
        response = client.get("/api/notes/does-not-exist")
        assert response.status_code == 404


class TestUpdateNote:
    def test_persists_edit_roundtrip(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo)
        edited = {**_SOAP, "subjective": "edited"}

        response = client.patch(
            f"/api/notes/{note.id}",
            json={"content_edited": edited},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["content_edited"] == edited

        # And it's persisted on the next read.
        followup = client.get(f"/api/notes/{note.id}")
        assert followup.status_code == 200
        assert followup.json()["content_edited"] == edited

    def test_returns_404_when_missing(self, client: TestClient) -> None:
        response = client.patch("/api/notes/missing", json={"content_edited": _SOAP})
        assert response.status_code == 404


class TestFinalizeNote:
    def test_finalizes_with_quality_rating(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo)
        response = client.post(
            f"/api/notes/{note.id}/finalize",
            json={"quality_rating": 4, "quality_rating_reason": "Good"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["quality_rating"] == 4
        assert body["finalized_at"] is not None

    def test_returns_409_on_double_finalize(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo, finalized=True)
        response = client.post(
            f"/api/notes/{note.id}/finalize",
            json={"quality_rating": 4},
        )
        assert response.status_code == 409


class TestSubmitForExport:
    def test_queues_finalized_note(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo, finalized=True)
        response = client.post(f"/api/notes/{note.id}/submit-export")
        assert response.status_code == 200
        body = response.json()
        assert body["export_status"] == "queued"
        assert body["export_queued_at"] is not None

    def test_rejects_unfinalized(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo)
        response = client.post(f"/api/notes/{note.id}/submit-export")
        assert response.status_code == 400


def _seed_patient(
    patient_repo: InMemoryPatientRepository,
    *,
    user_id: str,
    patient_id: str = "patient-1",
) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=patient_id,
        user_id=user_id,
        first_name="Jane",
        last_name="Doe",
        created_at=now,
        updated_at=now,
    )
    return patient_repo.create(patient)


class _StubGenerator(NoteGenerationService):
    """Deterministic stub that records its call args."""

    def __init__(self, content: dict[str, Any]) -> None:
        self.content = content
        self.last_call: dict[str, Any] | None = None

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> GeneratedNote:
        self.last_call = {
            "note_type": note_type,
            "transcript": transcript,
            "patient": patient,
            "session_date": session_date,
        }
        return GeneratedNote(note_type=note_type, content=self.content)


class TestCreateStandaloneNote:
    def test_creates_note_without_dictation(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)

        response = client.post(
            f"/api/patients/{patient.id}/notes",
            json={"note_type": "soap"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["patient_id"] == patient.id
        assert body["session_id"] is None
        assert body["note_type"] == "soap"
        assert body["content"] is None
        assert body["content_edited"] is None

        stored = mock_notes_repo.list_by_patient(patient.id)
        assert len(stored) == 1
        assert stored[0].session_id is None
        assert stored[0].content is None

    def test_creates_note_with_dictation_runs_generation(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        generated_content = {
            "subjective": {"chief_complaint": "Generated content"},
        }
        stub = _StubGenerator(generated_content)
        app.dependency_overrides[get_note_generation_service] = lambda: stub

        try:
            response = client.post(
                f"/api/patients/{patient.id}/notes",
                json={
                    "note_type": "narrative",
                    "dictation_transcript": {
                        "format": "txt",
                        "content": "Client reported...",
                    },
                },
            )
        finally:
            app.dependency_overrides.pop(get_note_generation_service, None)

        assert response.status_code == 201
        body = response.json()
        assert body["content"] == generated_content
        assert body["session_id"] is None
        assert stub.last_call is not None
        assert stub.last_call["note_type"] == "narrative"
        assert stub.last_call["transcript"].content == "Client reported..."
        assert stub.last_call["patient"].id == patient.id
        # session_date defaulted to now → naive comparison: tz-aware datetime
        assert stub.last_call["session_date"].tzinfo is not None

    def test_unknown_note_type_returns_400(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)

        response = client.post(
            f"/api/patients/{patient.id}/notes",
            json={"note_type": "dap"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNKNOWN_NOTE_TYPE"

    def test_authorizer_rejection_returns_403(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)

        class _Deny(NoteTypeAuthorizer):
            def is_allowed(self, user, note_type):  # type: ignore[no-untyped-def]
                return False

        deny_instance = _Deny()
        app.dependency_overrides[get_note_type_authorizer] = lambda: deny_instance
        try:
            response = client.post(
                f"/api/patients/{patient.id}/notes",
                json={"note_type": "soap"},
            )
        finally:
            app.dependency_overrides.pop(get_note_type_authorizer, None)

        assert response.status_code == 403

    def test_unknown_patient_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/patients/does-not-exist/notes",
            json={"note_type": "soap"},
        )
        assert response.status_code == 404

    def test_accepts_initial_content_edited(
        self,
        client: TestClient,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        patient = _seed_patient(mock_repo, user_id=mock_user_id)
        edited = {"narrative": {"body": "Clinician started here"}}

        response = client.post(
            f"/api/patients/{patient.id}/notes",
            json={"note_type": "narrative", "content_edited": edited},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["content_edited"] == edited
        assert body["content"] is None


class TestRequiresAuth:
    """All /api/notes endpoints require BAA acceptance.

    The conftest fixture overrides ``require_baa_acceptance`` so a logged-in
    user is implied; this test just smoke-checks that the dependency is
    wired (a 401 would surface as a different status).
    """

    def test_get_does_not_500(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _seed_note(mock_notes_repo)
        response = client.get(f"/api/notes/{note.id}")
        assert response.status_code != 500


class TestIDOR:
    """Regression tests for the cross-clinician note IDOR that motivated
    the patient_clinicians model.

    In the multi-clinician "Practice" edition (and in any tenant where
    two clinicians ever land in the same Postgres schema), clinician A
    must not be able to read, edit, finalize, or queue-for-export any
    note that belongs to clinician B's patient — even if A guesses or
    leaks B's note UUID. The conftest's ``mock_user`` is always the
    requesting user; we put the note under a *different* clinician's
    patient and assert each verb returns 404 (not 200, not 403 — 403
    would leak an existence oracle).

    These tests skip the conftest's grant_all_access shortcut by
    constructing a fresh ``InMemoryNotesRepository`` and granting only
    the foreign clinician access to the foreign patient.
    """

    @staticmethod
    def _override_notes_repo(repo: InMemoryNotesRepository) -> None:
        app.dependency_overrides[get_sessions_notes_repository] = lambda: repo
        app.dependency_overrides[get_notes_route_notes_repository] = lambda: repo

    @staticmethod
    def _cleanup_overrides() -> None:
        app.dependency_overrides.pop(get_sessions_notes_repository, None)
        app.dependency_overrides.pop(get_notes_route_notes_repository, None)

    @staticmethod
    def _seed_foreign_note(
        repo: InMemoryNotesRepository,
        *,
        finalized: bool,
    ) -> Note:
        """Add a note belonging to 'other-clinician' and grant only them access."""
        now = datetime.now(UTC)
        other_patient = "foreign-patient"
        other_clinician = "other-clinician"
        repo.grant_access(other_patient, other_clinician)

        note = Note(
            id=str(uuid.uuid4()),
            patient_id=other_patient,
            session_id=str(uuid.uuid4()),
            note_type="soap",
            content=_SOAP,
            created_at=now,
            updated_at=now,
        )
        if finalized:
            note.finalized_at = now
            note.quality_rating = 4
        return repo.add(note, other_clinician)

    def test_get_other_clinicians_note_returns_404(self, client: TestClient) -> None:
        repo = InMemoryNotesRepository()
        self._override_notes_repo(repo)
        try:
            note = self._seed_foreign_note(repo, finalized=False)
            response = client.get(f"/api/notes/{note.id}")
            # 404, not 403 — denial is indistinguishable from absence,
            # so the requester can't enumerate existing note IDs.
            assert response.status_code == 404
        finally:
            self._cleanup_overrides()

    def test_patch_other_clinicians_note_returns_404(self, client: TestClient) -> None:
        repo = InMemoryNotesRepository()
        self._override_notes_repo(repo)
        try:
            note = self._seed_foreign_note(repo, finalized=False)
            response = client.patch(
                f"/api/notes/{note.id}",
                json={"content_edited": {**_SOAP, "subjective": "forged"}},
            )
            assert response.status_code == 404
            # Defense-in-depth: confirm the underlying note was NOT mutated.
            stored = repo._notes[note.id]  # type: ignore[attr-defined]  # private read for assertion
            assert stored.content_edited is None
        finally:
            self._cleanup_overrides()

    def test_finalize_other_clinicians_note_returns_404(
        self, client: TestClient
    ) -> None:
        repo = InMemoryNotesRepository()
        self._override_notes_repo(repo)
        try:
            note = self._seed_foreign_note(repo, finalized=False)
            response = client.post(
                f"/api/notes/{note.id}/finalize",
                json={"quality_rating": 5},
            )
            assert response.status_code == 404
            stored = repo._notes[note.id]  # type: ignore[attr-defined]
            assert stored.finalized_at is None
            assert stored.quality_rating is None
        finally:
            self._cleanup_overrides()

    def test_submit_export_other_clinicians_note_returns_404(
        self, client: TestClient
    ) -> None:
        repo = InMemoryNotesRepository()
        self._override_notes_repo(repo)
        try:
            note = self._seed_foreign_note(repo, finalized=True)
            response = client.post(f"/api/notes/{note.id}/submit-export")
            assert response.status_code == 404
            stored = repo._notes[note.id]  # type: ignore[attr-defined]
            assert stored.export_status != "queued"
        finally:
            self._cleanup_overrides()


# Avoid unused-fixture warnings.
_ = pytest
