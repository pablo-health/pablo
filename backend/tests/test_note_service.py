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

_USER = "clinician-1"


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    _repo = InMemoryNotesRepository()
    _repo.grant_all_access()
    return _repo


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
            user_id=_USER,
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
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        # Add an in-progress edit to ensure regeneration clears it.
        service.update_note_edits(first.id, {"subjective": "edited"}, _USER)

        new_content = {**_SOAP, "subjective": "S2"}
        updated = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=new_content,
            user_id=_USER,
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
            user_id=_USER,
        )
        assert note.content is None
        assert note.note_type == "narrative"


class TestGetNote:
    def test_returns_note_when_exists(self, service: NoteService) -> None:
        sid = _new_session_id()
        added = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        assert service.get_note(added.id, _USER).id == added.id

    def test_raises_when_missing(self, service: NoteService) -> None:
        with pytest.raises(NoteNotFoundError):
            service.get_note("missing", _USER)


class TestGetByAndListByPatient:
    def test_get_note_by_session_id_returns_match(self, service: NoteService) -> None:
        sid = _new_session_id()
        service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        assert service.get_note_by_session_id(sid, _USER) is not None

    def test_get_note_by_session_id_returns_none_when_absent(self, service: NoteService) -> None:
        assert service.get_note_by_session_id("missing", _USER) is None

    def test_list_notes_for_patient(self, service: NoteService) -> None:
        for _ in range(3):
            service.create_or_update_for_session(
                session_id=_new_session_id(),
                patient_id="p1",
                note_type="soap",
                content=_SOAP,
                user_id=_USER,
            )
        service.create_or_update_for_session(
            session_id=_new_session_id(),
            patient_id="p2",
            note_type="soap",
            content=_SOAP,
            user_id=_USER,
        )

        for_p1 = service.list_notes_for_patient("p1", _USER)
        for_p2 = service.list_notes_for_patient("p2", _USER)
        assert len(for_p1) == 3
        assert len(for_p2) == 1


class TestUpdateNoteEdits:
    def test_persists_edits(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        edited = service.update_note_edits(
            note.id, {**_SOAP, "subjective": "edited"}, _USER
        )
        assert edited.content_edited is not None
        assert edited.content_edited["subjective"] == "edited"

    def test_raises_when_missing(self, service: NoteService) -> None:
        with pytest.raises(NoteNotFoundError):
            service.update_note_edits("missing", {}, _USER)


class TestFinalizeNote:
    def test_records_quality_rating_and_finalized_at(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        finalized = service.finalize_note(note.id, quality_rating=4, user_id=_USER)
        assert finalized.quality_rating == 4
        assert finalized.finalized_at is not None

    def test_rejects_double_finalize(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        service.finalize_note(note.id, quality_rating=4, user_id=_USER)
        with pytest.raises(NoteAlreadyFinalizedError):
            service.finalize_note(note.id, quality_rating=5, user_id=_USER)


class TestUpdateQualityRating:
    def test_returns_old_rating(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        service.finalize_note(note.id, quality_rating=4, user_id=_USER)
        updated, old = service.update_quality_rating(
            note.id, quality_rating=2, user_id=_USER
        )
        assert updated.quality_rating == 2
        assert old == 4

    def test_rejects_unfinalized(self, service: NoteService) -> None:
        sid = _new_session_id()
        note = service.create_or_update_for_session(
            session_id=sid, patient_id="p1", note_type="soap", content=_SOAP,
            user_id=_USER,
        )
        with pytest.raises(NoteNotFinalizedError):
            service.update_quality_rating(note.id, quality_rating=2, user_id=_USER)
