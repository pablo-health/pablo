# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The import route files a document as the note type the clinician picked.

SOAP when none is named; otherwise any built-in or active practice type a
visit note can be started from. The parse itself is a recording stand-in, so
these check what the route resolves and stores, not the model.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from app.main import app
from app.models import Patient
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import InMemoryPracticeNoteTypeRepository
from app.routes import notes as notes_routes
from app.routes import sessions as sessions_routes
from app.services.note_import_service import ParsedImportedNote
from app.services.note_service import NoteService
from app.services.session_service import SessionService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )

_TEMPLATE = TEMPLATES_DIR / "psychiatric_follow_up.json"
_FOLLOW_UP = "custom.psychiatric_follow_up"


class _RecordingParse:
    def __init__(self) -> None:
        self.definitions: list[NoteTypeDefinition] = []

    def parse_note(self, source_text: str, definition: NoteTypeDefinition) -> ParsedImportedNote:
        self.definitions.append(definition)
        first = definition.sections[0]
        return ParsedImportedNote(
            content={first.key: {first.fields[0].key: source_text}},
            session_date=None,
            session_time=None,
        )


@pytest.fixture
def parse() -> _RecordingParse:
    return _RecordingParse()


@pytest.fixture
def practice_types() -> InMemoryPracticeNoteTypeRepository:
    repo = InMemoryPracticeNoteTypeRepository()
    spec = json.loads(_TEMPLATE.read_text())["spec"]
    repo.add_version(_FOLLOW_UP, spec, "test-user-123", datetime.now(UTC))
    repo.add_version("custom.retired_type", spec, "test-user-123", datetime.now(UTC))
    repo.retire("custom.retired_type", datetime.now(UTC))
    return repo


@pytest.fixture
def import_url(
    client: Any,
    parse: _RecordingParse,
    practice_types: InMemoryPracticeNoteTypeRepository,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> Iterator[str]:
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        session_count=0,
        last_session_date=None,
    )
    mock_repo.create(patient, mock_user_id)
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: practice_types))
    app.dependency_overrides[notes_routes.get_registry] = lambda: registry
    app.dependency_overrides[sessions_routes.get_note_import_service] = lambda: parse
    app.dependency_overrides[sessions_routes.get_session_service] = lambda: SessionService(
        mock_session_repo, mock_repo, Mock(), NoteService(mock_notes_repo)
    )
    yield f"/api/patients/{patient.id}/sessions/import"
    app.dependency_overrides.pop(notes_routes.get_registry, None)


def _note_file() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("note.txt", b"Chief complaint: Trouble sleeping.", "text/plain")}


def test_a_note_with_no_type_named_is_imported_as_soap(
    client: Any, import_url: str, parse: _RecordingParse
) -> None:
    response = client.post(import_url, files=_note_file())

    assert response.status_code == 201, response.text
    assert [d.key for d in parse.definitions] == ["soap"]
    assert response.json()["note"]["note_type"] == "soap"


def test_a_practice_type_is_parsed_and_filed_as_that_type(
    client: Any, import_url: str, parse: _RecordingParse
) -> None:
    response = client.post(import_url, files=_note_file(), data={"note_type": _FOLLOW_UP})

    assert response.status_code == 201, response.text
    assert [d.key for d in parse.definitions] == [_FOLLOW_UP]
    note = response.json()["note"]
    assert note["note_type"] == _FOLLOW_UP
    assert note["note_type_version"] == 1
    assert note["content"] == {"encounter": {"visit_details": "Chief complaint: Trouble sleeping."}}


def test_a_built_in_type_is_accepted(client: Any, import_url: str, parse: _RecordingParse) -> None:
    response = client.post(import_url, files=_note_file(), data={"note_type": "narrative"})

    assert response.status_code == 201, response.text
    assert response.json()["note"]["note_type"] == "narrative"


@pytest.mark.parametrize(
    "note_type", ["no_such_type", "custom.no_such_type", "custom.retired_type"]
)
def test_an_unknown_type_is_refused_before_the_document_is_read(
    client: Any, import_url: str, parse: _RecordingParse, note_type: str
) -> None:
    response = client.post(import_url, files=_note_file(), data={"note_type": note_type})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_NOTE_TYPE"
    assert response.json()["error"]["message"] == "That note type isn't available."
    assert parse.definitions == []


@pytest.mark.parametrize("note_type", ["psychotherapy", "medications"])
def test_a_type_no_visit_note_starts_from_is_refused(
    client: Any, import_url: str, parse: _RecordingParse, note_type: str
) -> None:
    response = client.post(import_url, files=_note_file(), data={"note_type": note_type})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NOTE_TYPE_NOT_IMPORTABLE"
    assert parse.definitions == []
