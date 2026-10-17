# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's pending chart proposals, read from the chart rather than the note.

Grouped by the note that made them, newest note first; only what is still
pending; an archive-imported note's group names the system and the visit it
records. Decisions still go through the note's route, and one made there
leaves this listing the next time it is read.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_history.dependencies import get_chart_history_repository
from app.chart_proposals.dependencies import get_chart_proposal_repository
from app.chart_proposals.models import DraftedProposal, Evidence
from app.chart_proposals.service import ChartProposalService
from app.main import app
from app.models import Patient
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import ChartContext
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPracticeNoteTypeRepository,
)
from app.routes import notes as notes_routes
from app.services.note_service import NoteService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import InMemoryNotesRepository, InMemoryPatientRepository
    from fastapi.testclient import TestClient

FOLLOW_UP = "custom.psychiatric_follow_up"


@pytest.fixture
def proposals() -> Iterator[InMemoryChartProposalRepository]:
    repo = InMemoryChartProposalRepository()
    history = InMemoryChartHistoryRepository()
    practice_types = InMemoryPracticeNoteTypeRepository()
    spec = json.loads((TEMPLATES_DIR / "psychiatric_follow_up.json").read_text())["spec"]
    practice_types.add_version(FOLLOW_UP, spec, "test-user-123", datetime.now(UTC))
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: practice_types))
    overrides: dict[Any, Any] = {
        get_chart_history_repository: lambda: history,
        get_chart_proposal_repository: lambda: repo,
        notes_routes.get_registry: lambda: registry,
    }
    app.dependency_overrides.update(overrides)
    yield repo
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def patient(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> Patient:
    now = datetime.now(UTC)
    return mock_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Lulu",
            last_name="Llama",
            created_at=now,
            updated_at=now,
        ),
        mock_user_id,
    )


def _proposed(
    notes_repo: InMemoryNotesRepository,
    proposals: InMemoryChartProposalRepository,
    patient: Patient,
    user_id: str,
    *,
    field_key: str,
    text: str,
    content: dict[str, Any] | None = None,
    note_type: str = FOLLOW_UP,
) -> str:
    """A note of ``note_type`` with one pending proposal on ``field_key``."""
    note = NoteService(notes_repo).create_standalone_note(
        patient_id=patient.id,
        note_type=note_type,
        content=content or {"social_history": {}},
        user_id=user_id,
        note_type_version=1 if note_type == FOLLOW_UP else None,
    )
    ChartProposalService(proposals).refresh(
        note,
        ChartContext(),
        {},
        [
            DraftedProposal(
                field_key=field_key,
                proposed_text=text,
                what_changed="Stated in the note",
                evidence=(Evidence(2, "Client: Update on supports: Sister and a neighbor."),),
                origin="document",
            )
        ],
    )
    return note.id


def test_pending_proposals_are_grouped_by_note_newest_first_and_decided_ones_left_out(
    client: TestClient,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    proposals: InMemoryChartProposalRepository,
    patient: Patient,
) -> None:
    older = _proposed(
        mock_notes_repo, proposals, patient, mock_user_id, field_key="supports", text="A sister."
    )
    newer = _proposed(
        mock_notes_repo,
        proposals,
        patient,
        mock_user_id,
        field_key="work_school",
        text="Teaches fourth grade.",
    )
    # A proposal decided on the note is no longer waiting.
    (decided,) = proposals.list_for_note(older)
    client.post(
        f"/api/notes/{older}/chart-proposals/{decided.id}/decision", json={"decision": "discard"}
    ).raise_for_status()
    newest = _proposed(
        mock_notes_repo,
        proposals,
        patient,
        mock_user_id,
        field_key="relationships",
        text="Married, two children.",
    )

    response = client.get(f"/api/patients/{patient.id}/chart-proposals")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pending"] == 2
    assert {g["note_id"] for g in body["groups"]} == {newer, newest}
    assert all(len(g["proposals"]) == 1 for g in body["groups"])
    assert {p["field_key"] for g in body["groups"] for p in g["proposals"]} == {
        "work_school",
        "relationships",
    }
    assert all(p["decision"] == "pending" for g in body["groups"] for p in g["proposals"])
    assert all(g["imported"] is False and g["source_system"] is None for g in body["groups"])
    # Newest note first, by the date the group shows.
    dates = [g["date"] for g in body["groups"]]
    assert dates == sorted(dates, reverse=True)


def test_an_archive_imported_note_s_group_names_its_system_and_its_visit(
    client: TestClient,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    proposals: InMemoryChartProposalRepository,
    patient: Patient,
) -> None:
    _proposed(
        mock_notes_repo,
        proposals,
        patient,
        mock_user_id,
        field_key="supports",
        text="Sister and a neighbor.",
        note_type="narrative",
        content={
            "note": {"body": "Supports: sister and a neighbor."},
            "__source": {
                "system": "simplepractice",
                "kind": "progress",
                "title": "Progress Note",
                "visit": {"starts_at": "2026-09-25T14:00:00-04:00"},
            },
        },
    )

    body = client.get(f"/api/patients/{patient.id}/chart-proposals").json()

    (group,) = body["groups"]
    assert group["imported"] is True
    assert group["source_system"] == "simplepractice"
    assert group["date"].startswith("2026-09-25T14:00:00")
    assert group["proposals"][0]["evidence"][0]["segment_id"] == 2


def test_a_client_with_nothing_pending_answers_empty(client: TestClient, patient: Patient) -> None:
    assert client.get(f"/api/patients/{patient.id}/chart-proposals").json() == {
        "groups": [],
        "pending": 0,
    }


def test_another_user_s_client_is_not_found(
    client: TestClient, mock_repo: InMemoryPatientRepository
) -> None:
    now = datetime.now(UTC)
    other = mock_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Not",
            last_name="Mine",
            created_at=now,
            updated_at=now,
        ),
        "someone-else",
    )

    assert client.get(f"/api/patients/{other.id}/chart-proposals").status_code == 404
