# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A note an archive import landed is a reference copy, with or without a visit.

The archive importer writes where a note came from into its content under
``__source``. A note with a visit hangs off an ``imported`` session; a chart
note, an administrative note, a treatment plan or a psychotherapy note has no
visit and no session, and is told apart by that provenance alone. The note API
reads it out as ``source``, marks the note ``imported``, and every path that
signs or changes a signed note refuses it, as it refuses a single-document
import.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.models import Note, Patient
from app.models.note_source import note_source
from app.models.notes import NoteResponse
from app.services.note_service import NoteService

if TYPE_CHECKING:
    from app.repositories import InMemoryNotesRepository, InMemoryPatientRepository
    from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 26, 23, 30, tzinfo=UTC)

CHART_NOTE_SOURCE: dict[str, Any] = {
    "system": "simplepractice",
    "source_id": "1007836361",
    "kind": "chart",
    "title": "Chart Note",
    "signed_by": None,
    "signed_at": None,
    "locked": False,
    "created_at": "2026-09-26T23:30:00-04:00",
    "updated_at": "2026-09-26T23:30:00-04:00",
    "diagnoses": [],
    "billing_code": None,
    "billing_description": None,
    "visit": None,
}

PROGRESS_NOTE_SOURCE: dict[str, Any] = {
    **CHART_NOTE_SOURCE,
    "source_id": "1007836365",
    "kind": "progress",
    "title": "Progress Note",
    "signed_by": "Avery Provider",
    "signed_at": "2026-09-27T10:44:00-04:00",
    "locked": True,
    "diagnoses": [
        {"code": "F41.1", "description": "Generalized anxiety disorder"},
        {"code": "F33.1", "description": "Major depressive disorder, recurrent, moderate"},
    ],
    "billing_code": "90834",
    "visit": {
        "kind": "Individual",
        "starts_at": "2026-09-25T14:00:00-04:00",
        "ends_at": "2026-09-25T15:00:00-04:00",
        "duration_minutes": 60,
    },
}


def _note(content: dict[str, Any] | None, content_edited: dict[str, Any] | None = None) -> Note:
    return Note(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        note_type="narrative",
        created_at=NOW,
        updated_at=NOW,
        content=content,
        content_edited=content_edited,
    )


class TestNoteSource:
    def test_reads_the_provenance_an_archive_import_wrote(self) -> None:
        source = note_source(
            _note({"note": {"body": "Client reports…"}, "__source": PROGRESS_NOTE_SOURCE})
        )

        assert source is not None
        assert source.system == "simplepractice"
        assert source.kind == "progress"
        assert source.title == "Progress Note"
        assert source.signed_by == "Avery Provider"
        assert source.signed_at == datetime.fromisoformat("2026-09-27T10:44:00-04:00")
        assert source.visit_date == datetime.fromisoformat("2026-09-25T14:00:00-04:00")
        assert source.diagnoses == (
            ("F41.1", "Generalized anxiety disorder"),
            ("F33.1", "Major depressive disorder, recurrent, moderate"),
        )

    def test_a_restricted_note_carries_it_in_the_author_s_edit(self) -> None:
        # A psychotherapy note may only start empty; the importer lands its text,
        # provenance included, as content_edited.
        note = _note(None, {"note": {"body": "Own notes."}, "__source": CHART_NOTE_SOURCE})

        source = note_source(note)

        assert source is not None
        assert source.kind == "chart"
        assert source.visit_date is None
        assert source.created_at == datetime.fromisoformat("2026-09-26T23:30:00-04:00")

    @pytest.mark.parametrize(
        "content",
        [
            None,
            {"note": {"body": "Written here."}},
            {"note": {"body": "x"}, "__source": "simplepractice"},
            {"note": {"body": "x"}, "__source": {"kind": "chart"}},
            {"note": {"body": "x"}, "__source": {"system": ""}},
        ],
        ids=["no-content", "written-here", "not-a-dict", "no-system", "empty-system"],
    )
    def test_a_note_written_here_has_none(self, content: dict[str, Any] | None) -> None:
        assert note_source(_note(content)) is None

    def test_a_malformed_value_is_read_as_absent_not_as_an_error(self) -> None:
        source = note_source(
            _note(
                {
                    "__source": {
                        "system": "simplepractice",
                        "signed_at": "not a date",
                        "visit": "tomorrow",
                        "diagnoses": [{"description": "no code"}, "F41.1", {"code": "F33.1"}],
                    }
                }
            )
        )

        assert source is not None
        assert source.signed_at is None
        assert source.visit_date is None
        assert source.diagnoses == (("F33.1", ""),)


class TestNoteResponse:
    def test_a_sourced_note_is_imported_and_says_where_from(self) -> None:
        response = NoteResponse.from_note(
            _note({"note": {"body": "…"}, "__source": PROGRESS_NOTE_SOURCE})
        )

        assert response.imported is True
        assert response.source is not None
        assert response.source.system == "simplepractice"
        assert response.source.title == "Progress Note"
        assert response.source.signed_by == "Avery Provider"
        assert response.source.visit_date == datetime.fromisoformat("2026-09-25T14:00:00-04:00")
        assert [d.code for d in response.source.diagnoses] == ["F41.1", "F33.1"]

    def test_a_note_written_here_is_neither(self) -> None:
        response = NoteResponse.from_note(_note({"note": {"body": "Written here."}}))

        assert response.imported is False
        assert response.source is None


@pytest.fixture
def chart_note(
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> Note:
    """A chart note the archive importer landed: standalone, finalized, sourced."""
    patient = mock_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Lulu",
            last_name="Llama",
            created_at=NOW,
            updated_at=NOW,
        ),
        mock_user_id,
    )
    service = NoteService(mock_notes_repo)
    note = service.create_standalone_note(
        patient_id=patient.id,
        note_type="narrative",
        content={
            "note": {"body": "Client left a voicemail asking to move next week's appointment."},
            "__source": CHART_NOTE_SOURCE,
        },
        user_id=mock_user_id,
    )
    service.finalize_note(note.id, finalized_at=NOW, user_id=mock_user_id)
    finalized = mock_notes_repo.get(note.id)
    assert finalized is not None
    return finalized


def test_the_api_lists_an_archive_note_as_imported_with_its_source(
    client: TestClient, chart_note: Note
) -> None:
    single = client.get(f"/api/notes/{chart_note.id}").json()
    listed = client.get(f"/api/patients/{chart_note.patient_id}/notes").json()["data"]

    assert single["imported"] is True
    assert single["source"]["system"] == "simplepractice"
    assert single["source"]["title"] == "Chart Note"
    assert single["source"]["visit_date"] is None
    assert [(n["id"], n["imported"]) for n in listed] == [(chart_note.id, True)]


SIGNER_FIELDS = {"signer_name": "Someone Here", "signer_credentials": "LCSW"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/notes/{note}/sign", SIGNER_FIELDS),
        ("/api/notes/{note}/unlock", {"reason": "Correct a typo"}),
        ("/api/notes/{note}/addenda", {"text": "Later detail.", **SIGNER_FIELDS}),
    ],
    ids=["sign", "unlock", "addendum"],
)
def test_signing_paths_refuse_an_archive_note_that_has_no_session(
    client: TestClient,
    chart_note: Note,
    mock_notes_repo: InMemoryNotesRepository,
    path: str,
    body: dict[str, Any],
) -> None:
    response = client.post(path.format(note=chart_note.id), json=body)

    assert response.status_code == 409, response.text
    assert "imported note" in response.text
    assert mock_notes_repo.list_signatures(chart_note) == []
