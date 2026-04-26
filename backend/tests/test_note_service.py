# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for NoteService (pa-0nx.2)."""

from __future__ import annotations

import uuid

import pytest
from app.repositories import InMemoryNotesRepository
from app.services.note_service import (
    NoteAlreadyFinalizedError,
    NoteNotFinalizedError,
    NoteNotFoundError,
    NoteService,
)


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    return InMemoryNotesRepository()


@pytest.fixture
def service(notes_repo: InMemoryNotesRepository) -> NoteService:
    return NoteService(notes_repo)


def _new_session_id() -> str:
    return str(uuid.uuid4())


_SOAP = {
    "subjective": "S",
    "objective": "O",
    "assessment": "A",
    "plan": "P",
}


class TestCreateOrUpdateForSession:
    def test_creates_new_note_when_none_exists(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid,
            patient_id="p1",
            note_type="soap",
            content=_SOAP,
        )
        assert note.session_id == sid
        assert note.patient_id == "p1"
        assert note.content == _SOAP
        assert note.content_edited is None

    def test_updates_existing_note_when_session_already_has_one(
        self, service: NoteService
    ) -> None:
        sid = _new_session_id()
        first = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        # Add an in-progress edit to ensure regeneration clears it.
        service.update_note_edits(first.id, {"subjective": "edited"})

        new_content = {**_SOAP, "subjective": "S2"}
        updated = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=new_content
        )
        assert updated.id == first.id  # same row, updated
        assert updated.content == new_content
        assert updated.content_edited is None

    def test_pre_allocates_with_no_content(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid,
            patient_id="p1",
            note_type="narrative",
            content=None,
        )
        assert note.content is None
        assert note.note_type == "narrative"


class TestGetNote:
    def test_returns_note_when_exists(self, service: NoteService) -> None:
        sid = _new_session_id()
        added = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        assert service.get_note(added.id).id == added.id

    def test_raises_when_missing(self, service: NoteService) -> None:
        with pytest.raises(NoteNotFoundError):
            service.get_note("missing")


class TestGetByAndListByPatient:
    def test_get_note_by_session_id_returns_match(self, service: NoteService) -> None:
        sid = _new_session_id()
        service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        assert service.get_note_by_session_id(sid) is not None

    def test_get_note_by_session_id_returns_none_when_absent(self, service: NoteService) -> None:
        assert service.get_note_by_session_id("missing") is None

    def test_list_notes_for_patient(self, service: NoteService) -> None:
        for _ in range(3):
            service.create_or_update_for_session(
                session_id=_new_session_id(),
                patient_id="p1",
                note_type="soap",
                content=_SOAP,
            )
        service.create_or_update_for_session(
            session_id=_new_session_id(),
            patient_id="p2",
            note_type="soap",
            content=_SOAP,
        )

        for_p1 = service.list_notes_for_patient("p1")
        for_p2 = service.list_notes_for_patient("p2")
        assert len(for_p1) == 3
        assert len(for_p2) == 1


class TestUpdateNoteEdits:
    def test_persists_edits(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        edited = service.update_note_edits(note.id, {**_SOAP, "subjective": "edited"})
        assert edited.content_edited is not None
        assert edited.content_edited["subjective"] == "edited"

    def test_raises_when_missing(self, service: NoteService) -> None:
        with pytest.raises(NoteNotFoundError):
            service.update_note_edits("missing", {})


class TestFinalizeNote:
    def test_records_quality_rating_and_finalized_at(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        finalized = service.finalize_note(note.id, quality_rating=4)
        assert finalized.quality_rating == 4
        assert finalized.finalized_at is not None

    def test_rejects_double_finalize(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        service.finalize_note(note.id, quality_rating=4)
        with pytest.raises(NoteAlreadyFinalizedError):
            service.finalize_note(note.id, quality_rating=5)


class TestUpdateQualityRating:
    def test_returns_old_rating(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        service.finalize_note(note.id, quality_rating=4)
        updated, old = service.update_quality_rating(note.id, quality_rating=2)
        assert updated.quality_rating == 2
        assert old == 4

    def test_rejects_unfinalized(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        with pytest.raises(NoteNotFinalizedError):
            service.update_quality_rating(note.id, quality_rating=2)


class TestSubmitForExport:
    def test_queues_finalized_note(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        service.finalize_note(note.id, quality_rating=4)
        queued = service.submit_note_for_export(note.id)
        assert queued.export_status == "queued"
        assert queued.export_queued_at is not None

    def test_rejects_unfinalized(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP
        )
        with pytest.raises(NoteNotFinalizedError):
            service.submit_note_for_export(note.id)
