# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart-proposal API, and a session draft storing its proposals."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_history.dependencies import get_chart_history_repository
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.dependencies import get_chart_proposal_repository
from app.chart_proposals.models import DraftedProposal, Evidence
from app.chart_proposals.service import ChartProposalService
from app.chart_proposals.step import ChartProposalStep
from app.main import app
from app.models import Patient, SessionStatus, TherapySession, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import ChartContext
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientProblemRepository,
    InMemoryPracticeNoteTypeRepository,
)
from app.routes import notes as notes_routes
from app.services.note_generation_service import MockNoteGenerationService
from app.services.note_service import NoteService
from app.services.session_service import SessionService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from fastapi.testclient import TestClient

TEMPLATES = (
    Path(__file__).resolve().parents[2] / "frontend/src/components/settings/noteTypes/templates"
)
FOLLOW_UP = "custom.psychiatric_follow_up"
EVALUATION = "custom.psychiatric_evaluation"
SEPARATED = "Married; separated, divorce in progress since June."
FINALIZED = "Married; separated June; divorce finalized April 2."


@pytest.fixture
def chart() -> Iterator[tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository]]:
    history = InMemoryChartHistoryRepository()
    proposals = InMemoryChartProposalRepository()
    practice_types = InMemoryPracticeNoteTypeRepository()
    for key, template in (
        (FOLLOW_UP, "psychiatric_follow_up"),
        (EVALUATION, "psychiatric_evaluation"),
    ):
        spec = json.loads((TEMPLATES / f"{template}.json").read_text())["spec"]
        practice_types.add_version(key, spec, "test-user-123", datetime.now(UTC))
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: practice_types))
    app.dependency_overrides[get_chart_history_repository] = lambda: history
    app.dependency_overrides[get_chart_proposal_repository] = lambda: proposals
    app.dependency_overrides[notes_routes.get_registry] = lambda: registry
    yield history, proposals
    for dependency in (
        get_chart_history_repository,
        get_chart_proposal_repository,
        notes_routes.get_registry,
    ):
        app.dependency_overrides.pop(dependency, None)


def _patient(repo: InMemoryPatientRepository, user_id: str) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    patient.allergy_status = "nkda"
    return repo.create(patient, user_id)


def _follow_up_with_proposal(
    notes_repo: InMemoryNotesRepository,
    proposals: InMemoryChartProposalRepository,
    patient: Patient,
    user_id: str,
) -> str:
    note = NoteService(notes_repo).create_standalone_note(
        patient_id=patient.id,
        note_type=FOLLOW_UP,
        content={"social_history": {"relationships": SEPARATED}},
        user_id=user_id,
        note_type_version=1,
    )
    ChartProposalService(proposals).refresh(
        note,
        ChartContext(),
        {},
        [
            DraftedProposal(
                field_key="relationships",
                proposed_text=FINALIZED,
                what_changed="Divorce finalized",
                evidence=(Evidence(4, "[00:31] Client: The divorce was finalized on April 2."),),
            )
        ],
    )
    return note.id


def test_a_note_lists_its_proposals_with_the_chart_and_the_cited_lines(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    chart: tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository],
) -> None:
    history, proposals = chart
    patient = _patient(mock_repo, mock_user_id)
    ChartHistoryService(history).set(patient.id, "relationships", mock_user_id, SEPARATED)
    note_id = _follow_up_with_proposal(mock_notes_repo, proposals, patient, mock_user_id)

    response = client.get(f"/api/notes/{note_id}/chart-proposals")

    assert response.status_code == 200, response.text
    (proposal,) = response.json()["data"]
    assert proposal["label"] == "Social history and supports: Relationships"
    assert proposal["current_text"] == SEPARATED
    assert proposal["proposed_text"] == FINALIZED
    assert proposal["decision"] == "pending"
    assert proposal["evidence"] == [
        {"segment_id": 4, "text": "[00:31] Client: The divorce was finalized on April 2."}
    ]


def test_accept_edit_and_discard_through_the_api(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    chart: tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository],
) -> None:
    history, proposals = chart
    patient = _patient(mock_repo, mock_user_id)
    note_id = _follow_up_with_proposal(mock_notes_repo, proposals, patient, mock_user_id)
    (proposal,) = client.get(f"/api/notes/{note_id}/chart-proposals").json()["data"]
    url = f"/api/notes/{note_id}/chart-proposals/{proposal['id']}/decision"

    assert client.post(url, json={"decision": "edit"}).status_code == 422
    edited = client.post(url, json={"decision": "edit", "text": "Divorced April 2."})
    again = client.post(url, json={"decision": "discard"})

    assert edited.status_code == 200, edited.text
    assert edited.json()["decision"] == "edited"
    assert edited.json()["current_text"] == "Divorced April 2."
    assert (
        ChartHistoryService(history).entries(patient.id)["relationships"].source_note_id == note_id
    )
    assert again.status_code == 409
    assert (
        client.post(
            f"/api/notes/{note_id}/chart-proposals/nope/decision", json={"decision": "accept"}
        ).status_code
        == 404
    )


def test_another_clients_note_is_not_found(
    client: TestClient,
    chart: tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository],
) -> None:
    assert client.get(f"/api/notes/{uuid.uuid4()}/chart-proposals").status_code == 404


def test_a_saved_edit_recomputes_what_the_note_records_and_reading_changes_nothing(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    chart: tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository],
) -> None:
    _, proposals = chart
    patient = _patient(mock_repo, mock_user_id)
    note = NoteService(mock_notes_repo).create_standalone_note(
        patient_id=patient.id,
        note_type=EVALUATION,
        content={"social_history": {"relationships": "Married.", "supports": "Not stated."}},
        user_id=mock_user_id,
        note_type_version=1,
    )

    assert client.get(f"/api/notes/{note.id}/chart-proposals").json()["data"] == []
    assert proposals.list_for_note(note.id) == []

    saved = client.patch(
        f"/api/notes/{note.id}",
        json={"content_edited": {"social_history": {"relationships": "Married 19 years."}}},
    )
    body = client.get(f"/api/notes/{note.id}/chart-proposals").json()

    assert saved.status_code == 200, saved.text
    assert [(p["field_key"], p["proposed_text"], p["what_changed"]) for p in body["data"]] == [
        ("relationships", "Married 19 years.", "Recorded this visit")
    ]


class _Proposing(MockNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {
                "proposals": [
                    {
                        "field_key": "relationships",
                        "proposed_text": FINALIZED,
                        "what_changed": "Divorce finalized",
                        "evidence_segment_ids": [1],
                    }
                ]
            }

        return complete


def test_a_session_draft_stores_its_proposals_beside_the_note(
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    chart: tuple[InMemoryChartHistoryRepository, InMemoryChartProposalRepository],
) -> None:
    history, proposals = chart
    patient = _patient(mock_repo, mock_user_id)
    ChartHistoryService(history).set(patient.id, "relationships", mock_user_id, SEPARATED)
    registry = app.dependency_overrides[notes_routes.get_registry]()
    session = mock_session_repo.create(
        TherapySession(
            id=str(uuid.uuid4()),
            user_id=mock_user_id,
            patient_id=patient.id,
            session_date=datetime.now(UTC),
            session_number=1,
            status=SessionStatus.PROCESSING,
            transcript=Transcript(
                format="txt",
                content="[00:01] Therapist: Anything new?\n"
                "[00:05] Client: The divorce was finalized on April 2.",
            ),
            created_at=datetime.now(UTC),
        )
    )
    service = SessionService(
        mock_session_repo,
        mock_repo,
        _Proposing(registry),
        NoteService(mock_notes_repo),
        problem_repo=InMemoryPatientProblemRepository(),
        history_repo=history,
        proposal_step=ChartProposalStep(proposals, history),
    )
    definition = registry.get(FOLLOW_UP)

    note = service._generate_and_persist_note(
        session,
        patient,
        FOLLOW_UP,
        mock_user_id,
        definition=definition,
        chart=service._chart_for(patient, mock_user_id),
    )

    (stored,) = [p for p in proposals.list_for_note(note.id) if p.origin == "transcript"]
    assert (stored.field_key, stored.proposed_text) == ("relationships", FINALIZED)
    assert [e.text for e in stored.evidence] == [
        "[00:05] Client: The divorce was finalized on April 2."
    ]
    # The note's content carries no proposal.
    assert FINALIZED not in json.dumps(note.content)
