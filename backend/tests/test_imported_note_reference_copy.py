# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An imported note is a reference copy of a note signed elsewhere, never one to sign.

The visit was documented, and signed, in the system where it happened. So the
import lands in its own ``imported`` status with the details the document
states and who signed it there, every path that signs, locks, unlocks or adds
to a note refuses it, and what it proposes for the chart is still accepted the
ordinary way.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.chart_history.dependencies import get_chart_history_repository
from app.chart_proposals.dependencies import get_chart_proposal_repository
from app.main import app
from app.models import Patient
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientDocumentRepository,
    InMemoryPracticeNoteTypeRepository,
)
from app.routes import notes as notes_routes
from app.routes import sessions as sessions_routes
from app.routes.patient_documents import get_patient_documents_service
from app.services.file_storage import FileStorageProvider
from app.services.note_generation_service import MockNoteGenerationService
from app.services.note_import_service import ParsedImportedNote
from app.services.note_service import NoteService
from app.services.patient_documents_service import PatientDocumentsService
from app.services.session_service import SessionService
from app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from fastapi.testclient import TestClient

FOLLOW_UP = "custom.psychiatric_follow_up"
PATIENT_ID = str(uuid.uuid4())
SIGNER = "A. Rivera, PMHNP-BC"
DOCUMENT = "\n\n".join(
    [
        "PSYCHIATRIC FOLLOW-UP\nDate of service: 09/23/2026\nVisit: Telehealth video visit.",
        "SOCIAL HISTORY\nTeaches fourth grade.",
        f"Electronically signed by {SIGNER}",
    ]
)
P_SOCIAL = 1


class _Parse:
    def parse_note(self, source_text: str, definition: NoteTypeDefinition) -> ParsedImportedNote:
        return ParsedImportedNote(
            content={"social_history": {"work_school": "Teaches fourth grade."}},
            session_date=None,
            session_time=None,
            note_inputs={"place_of_service": "Telehealth"},
            signed_by=SIGNER,
        )


class _Proposing(MockNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {
                "proposals": [
                    {
                        "field_key": "work_school",
                        "proposed_text": "Teaches fourth grade.",
                        "what_changed": "Stated in the note",
                        "evidence_segment_ids": [P_SOCIAL],
                    }
                ]
            }

        return complete


@pytest.fixture
def chart_history() -> InMemoryChartHistoryRepository:
    return InMemoryChartHistoryRepository()


@pytest.fixture
def storage() -> MagicMock:
    """Document storage: records each write; a test makes one fail."""
    return MagicMock(spec=FileStorageProvider)


@pytest.fixture
def documents() -> InMemoryPatientDocumentRepository:
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access(PATIENT_ID, "test-user-123")
    return repo


@pytest.fixture
def importer(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    chart_history: InMemoryChartHistoryRepository,
    storage: MagicMock,
    documents: InMemoryPatientDocumentRepository,
) -> Iterator[Callable[[], Any]]:
    """Posts ``DOCUMENT`` to the import route as a psychiatric follow-up."""
    now = datetime.now(UTC)
    patient = mock_repo.create(
        Patient(
            id=PATIENT_ID,
            first_name="Sam",
            last_name="Sample",
            created_at=now,
            updated_at=now,
        ),
        mock_user_id,
    )
    practice_types = InMemoryPracticeNoteTypeRepository()
    spec = json.loads((TEMPLATES_DIR / "psychiatric_follow_up.json").read_text())["spec"]
    practice_types.add_version(FOLLOW_UP, spec, mock_user_id, now)
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: practice_types))
    generator = _Proposing()
    proposals = InMemoryChartProposalRepository()
    overrides: dict[Any, Any] = {
        notes_routes.get_registry: lambda: registry,
        notes_routes.get_note_generation_service: lambda: generator,
        sessions_routes.get_note_import_service: _Parse,
        sessions_routes.get_session_service: lambda: SessionService(
            mock_session_repo, mock_repo, generator, NoteService(mock_notes_repo)
        ),
        get_chart_proposal_repository: lambda: proposals,
        get_chart_history_repository: lambda: chart_history,
        get_patient_documents_service: lambda: PatientDocumentsService(
            repo=documents,
            settings=Settings(
                database_url="postgresql://test:test@localhost:5432/test",
                patient_documents_gcs_bucket="pablo-docs-test",
            ),
            storage=storage,
        ),
    }
    app.dependency_overrides.update(overrides)
    yield lambda: client.post(
        f"/api/patients/{patient.id}/sessions/import",
        files={"file": ("transfer.txt", DOCUMENT.encode(), "text/plain")},
        data={"note_type": FOLLOW_UP},
    )
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def imported(importer: Callable[[], Any]) -> dict[str, Any]:
    """The import route's response for a psychiatric follow-up read from ``DOCUMENT``."""
    response = importer()
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_an_import_lands_as_a_reference_copy_with_what_its_document_states(
    imported: dict[str, Any],
) -> None:
    assert imported["status"] == "imported"
    assert imported["source"] == "imported"
    # Who signed it there, as written; never a user of this practice.
    assert imported["source_signer"] == SIGNER
    assert imported["note"]["note_inputs"] == {"place_of_service": "Telehealth"}
    assert imported["note"]["finalized_at"] is None


def test_the_uploaded_file_is_kept_as_one_client_document_linked_to_the_note(
    imported: dict[str, Any],
    storage: MagicMock,
    documents: InMemoryPatientDocumentRepository,
) -> None:
    (kept,) = documents.list_for_patient(PATIENT_ID, "test-user-123")

    assert imported["source_document_id"] == kept.id
    assert (kept.filename, kept.mime_type, kept.size_bytes) == (
        "transfer.txt",
        "text/plain",
        len(DOCUMENT.encode()),
    )
    assert kept.finalized_at is not None
    storage.upload_bytes.assert_called_once_with(
        bucket="pablo-docs-test",
        object_name=kept.gcs_path,
        data=DOCUMENT.encode(),
        content_type="text/plain",
    )


def test_a_file_that_cannot_be_stored_leaves_no_session_behind(
    importer: Callable[[], Any],
    storage: MagicMock,
    documents: InMemoryPatientDocumentRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
) -> None:
    storage.upload_bytes.side_effect = OSError("storage unavailable")

    response = importer()

    assert response.status_code == 503, response.text
    # Read past the repo's access set: nothing at all was written.
    assert [s for s in mock_session_repo._sessions.values() if s.patient_id == PATIENT_ID] == []
    assert mock_notes_repo.list_by_patient(PATIENT_ID) == []
    assert documents.list_for_patient(PATIENT_ID, "test-user-123") == []


def test_the_client_s_notes_list_it_as_imported_not_as_a_draft(
    client: TestClient, imported: dict[str, Any]
) -> None:
    note_id = imported["note"]["id"]

    listed = client.get(f"/api/patients/{imported['patient_id']}/notes").json()["data"]
    single = client.get(f"/api/notes/{note_id}").json()

    assert [(n["id"], n["imported"]) for n in listed] == [(note_id, True)]
    assert single["imported"] is True
    assert imported["note"]["imported"] is True


SIGNER_FIELDS = {"signer_name": "Someone Here", "signer_credentials": "LCSW"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/notes/{note}/sign", SIGNER_FIELDS),
        ("post", "/api/notes/{note}/finalize", {}),
        ("post", "/api/notes/{note}/unlock", {"reason": "Correct a typo"}),
        ("post", "/api/notes/{note}/addenda", {"text": "Later detail.", **SIGNER_FIELDS}),
        ("patch", "/api/sessions/{session}/finalize", {"signature": SIGNER_FIELDS}),
    ],
    ids=["sign", "finalize-note", "unlock", "addendum", "finalize-session"],
)
def test_every_path_that_signs_or_changes_a_signed_note_refuses_it(
    client: TestClient,
    imported: dict[str, Any],
    mock_notes_repo: InMemoryNotesRepository,
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    url = path.format(note=imported["note"]["id"], session=imported["id"])

    response = getattr(client, method)(url, json=body)

    assert response.status_code == 409, response.text
    assert "imported note" in response.text
    note = mock_notes_repo.get(imported["note"]["id"])
    assert note is not None
    assert note.finalized_at is None
    assert mock_notes_repo.list_signatures(note) == []


def test_its_chart_proposals_are_accepted_without_a_signature(
    client: TestClient,
    imported: dict[str, Any],
    chart_history: InMemoryChartHistoryRepository,
) -> None:
    note_id = imported["note"]["id"]
    listed = client.get(f"/api/notes/{note_id}/chart-proposals").json()["data"]
    (work,) = [p for p in listed if p["field_key"] == "work_school"]

    decided = client.post(
        f"/api/notes/{note_id}/chart-proposals/{work['id']}/decision",
        json={"decision": "accept"},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["decision"] == "accepted"
    entries = {e.field_key: e for e in chart_history.entries(imported["patient_id"])}
    assert entries["work_school"].text == "Teaches fourth grade."
    assert entries["work_school"].source_note_id == note_id
    # Still a reference copy: accepting changed the chart, not the note.
    assert client.get(f"/api/sessions/{imported['id']}").json()["status"] == "imported"
