# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/notes (pa-0nx.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.models import Note
from app.repositories import InMemoryNotesRepository  # noqa: TC002 — runtime fixture type
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


# Avoid unused-fixture warnings.
_ = pytest
