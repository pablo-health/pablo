# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An imported note proposes chart updates from its document, decided at sign.

A note written in another records system has no transcript: the document is
read in its place, numbered a paragraph at a time, and a proposal must cite
the paragraphs that state it. A note often carries forward blocks written
at earlier visits; where one disagrees with the part written for this
visit, the proposal follows this visit and cites the carried paragraph as
well, and it wins over the note's own (carried) text for that field.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_history.dependencies import get_chart_history_repository
from app.chart_proposals.dependencies import get_chart_proposal_repository
from app.chart_proposals.drafting import (
    build_document_prompt,
    document_segments,
    propose_from_document,
)
from app.chart_proposals.families import MEDICATIONS
from app.chart_proposals.models import RECORDED_THIS_VISIT
from app.chart_proposals.service import ChartProposalService
from app.main import app
from app.models import Note, Patient
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import ChartContext, ChartHistoryField, ChartMedication
from app.notes.practice_types import RepositoryPracticeNoteTypeSource
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPracticeNoteTypeRepository,
)
from app.routes import notes as notes_routes
from app.routes import sessions as sessions_routes
from app.services.note_generation_service import MockNoteGenerationService
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
    from app.services.note_generation_service import CompleteStructured
    from fastapi.testclient import TestClient

FOLLOW_UP = "custom.psychiatric_follow_up"

CARRIED = (
    "SOCIAL HISTORY (carried forward from 03/02/2026)\n"
    "Works full time as a dental hygienist at a family dental practice."
)
INTERVAL = "INTERVAL HISTORY\nLaid off from the dental practice at the end of August."
CARRIED_MEDICATIONS = "CURRENT MEDICATIONS (carried forward)\nBuspirone 10 mg twice daily"
PLAN = "PLAN\nIncrease buspirone to 15 mg twice daily."
DOCUMENT = "\n\n".join(
    [
        "PSYCHIATRIC FOLLOW-UP\nDate of service: 09/23/2026",
        CARRIED,
        CARRIED_MEDICATIONS,
        INTERVAL,
        "SUBSTANCE USE\nAlcohol: two glasses of wine per week.",
        PLAN,
    ]
)
# The document's paragraphs as the proposal call numbers them.
P_CARRIED, P_INTERVAL, P_ALCOHOL = 1, 3, 4
LAID_OFF = "Worked full time as a dental hygienist until the end of August; laid off."

# What the import parse relocated: the carried block, as the note's own text.
PARSED: dict[str, Any] = {
    "social_history": {"work_school": CARRIED.splitlines()[1]},
    "substance_use": {"alcohol": "two glasses of wine per week."},
}


def _reply(*proposals: dict[str, Any]) -> dict[str, Any]:
    return {"proposals": list(proposals)}


def _work_school(ids: list[Any]) -> dict[str, Any]:
    return {
        "field_key": "work_school",
        "proposed_text": LAID_OFF,
        "what_changed": "Laid off in August",
        "evidence_segment_ids": ids,
    }


def _answering(*proposals: dict[str, Any]) -> CompleteStructured:
    def complete(_system: str, _user: str, _schema: dict[str, Any]) -> dict[str, Any]:
        return _reply(*proposals)

    return complete


# --- Reading the document -----------------------------------------------------------


def test_a_document_is_numbered_a_paragraph_at_a_time() -> None:
    segments = document_segments(DOCUMENT)

    assert segments[P_CARRIED] == CARRIED
    assert segments[P_INTERVAL] == INTERVAL
    assert len(segments) == 6
    prompt = build_document_prompt(ChartContext(), segments)
    assert f"[S{P_INTERVAL}] INTERVAL HISTORY\n    Laid off from the dental practice" in prompt


def test_a_document_with_no_blank_lines_has_a_paragraph_a_line() -> None:
    """A Word export puts each paragraph on a line of its own."""
    assert document_segments("Chief complaint: Sleep.\nPlan: Return in 6 weeks.\n") == {
        0: "Chief complaint: Sleep.",
        1: "Plan: Return in 6 weeks.",
    }


def test_a_proposal_keeps_the_paragraphs_it_cites_verbatim() -> None:
    drafted = propose_from_document(
        _answering(_work_school([P_INTERVAL])), ChartContext(), DOCUMENT
    )

    (proposal,) = drafted.proposals
    assert proposal.origin == "document"
    assert [(e.segment_id, e.text) for e in proposal.evidence] == [(P_INTERVAL, INTERVAL)]
    assert INTERVAL in DOCUMENT


@pytest.mark.parametrize(
    "ids",
    [[], [6], [P_INTERVAL, 99], None],
    ids=["none", "past-the-end", "one-not-in-the-document", "missing"],
)
def test_a_proposal_citing_a_paragraph_the_document_lacks_is_dropped(ids: Any) -> None:
    drafted = propose_from_document(_answering(_work_school(ids)), ChartContext(), DOCUMENT)
    assert drafted.proposals == []


def test_the_plan_wins_over_a_carried_block_and_the_conflict_is_cited() -> None:
    prompts: list[str] = []

    def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        return _reply(_work_school([P_INTERVAL, P_CARRIED]))

    (proposal,) = propose_from_document(complete, ChartContext(), DOCUMENT).proposals

    # Both paragraphs are shown, so the clinician sees what disagreed.
    assert [e.text for e in proposal.evidence] == [CARRIED, INTERVAL]
    assert "propose what the plan states" in prompts[0]
    assert "cite the paragraph that disagrees as well" in prompts[0]
    # Medications are the medication list's; the prompt keeps them out of history fields.
    assert "are kept on the chart's medication list, not in these fields" in prompts[0]


P_CARRIED_MEDICATIONS, P_PLAN = 2, 5
MEDICATION_REPLY = "medication_changes"
BUSPIRONE = ChartContext(medications=(ChartMedication("Buspirone", "10 mg", "twice daily"),))


def _medication_change(ids: list[Any]) -> dict[str, Any]:
    return {
        "action": "change",
        "drug_name": "buspirone",
        "dose": "15 mg",
        "what_changed": "Dose increased to 15 mg",
        "evidence_segment_ids": ids,
    }


def test_a_medication_change_follows_the_plan_and_cites_the_carried_list() -> None:
    prompts: list[str] = []

    def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        change = _medication_change([P_PLAN, P_CARRIED_MEDICATIONS])
        return {"proposals": [], MEDICATION_REPLY: [change]}

    (proposal,) = propose_from_document(complete, BUSPIRONE, DOCUMENT).proposals

    assert (proposal.field_key, proposal.item_key, proposal.origin) == (
        MEDICATIONS,
        "Buspirone",
        "document",
    )
    assert proposal.change is not None
    assert (proposal.change.action, proposal.change.dose) == ("change", "15 mg")
    assert [e.text for e in proposal.evidence] == [CARRIED_MEDICATIONS, PLAN]
    assert "the document's plan is the clinician's decision" in prompts[0]
    assert "only a carried block lists" in prompts[0]


def test_a_medication_citing_a_paragraph_the_document_lacks_is_dropped() -> None:
    def complete(_system: str, _user: str, _schema: dict[str, Any]) -> dict[str, Any]:
        return {"proposals": [], MEDICATION_REPLY: [_medication_change([P_PLAN, 99])]}

    assert propose_from_document(complete, BUSPIRONE, DOCUMENT).proposals == []


def test_a_failed_call_proposes_nothing_and_says_so() -> None:
    def fails(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model unavailable")

    failed = propose_from_document(fails, ChartContext(), DOCUMENT)
    assert (failed.proposals, failed.error_class) == ([], "RuntimeError")


# --- Storing them beside the note ---------------------------------------------------


def _note() -> Note:
    now = datetime.now(UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        note_type=FOLLOW_UP,
        created_at=now,
        updated_at=now,
        content=PARSED,
    )


def test_the_documents_proposal_wins_over_the_notes_carried_text() -> None:
    repo = InMemoryChartProposalRepository()
    service = ChartProposalService(repo)
    note = _note()
    drafted = propose_from_document(
        _answering(_work_school([P_INTERVAL, P_CARRIED])), ChartContext(), DOCUMENT
    ).proposals

    service.refresh(note, ChartContext(), PARSED, drafted)

    listed = {p.field_key: p for p in service.proposals(note.id)}
    assert (listed["work_school"].proposed_text, listed["work_school"].origin) == (
        LAID_OFF,
        "document",
    )
    assert [e.segment_id for e in listed["work_school"].evidence] == [P_CARRIED, P_INTERVAL]
    # A field the document proposed nothing for is still seeded from the note's text.
    assert (listed["alcohol"].what_changed, listed["alcohol"].origin) == (
        RECORDED_THIS_VISIT,
        "note",
    )

    # A clinician's edit recomputes the note's own proposals and keeps the document's.
    service.refresh(note, ChartContext(), PARSED)
    after_edit = {p.field_key: (p.proposed_text, p.origin) for p in service.proposals(note.id)}
    assert after_edit["work_school"] == (LAID_OFF, "document")

    # A retry that proposes nothing for the field hands it back to the note's text.
    service.refresh(note, ChartContext(), PARSED, [])
    retried = {p.field_key: (p.proposed_text, p.origin) for p in service.proposals(note.id)}
    assert retried["work_school"] == (PARSED["social_history"]["work_school"], "note")


def test_a_field_the_chart_already_has_is_proposed_only_from_the_document() -> None:
    chart = ChartContext(
        history=(
            ChartHistoryField(
                "work_school", PARSED["social_history"]["work_school"], date(2026, 3, 2)
            ),
        )
    )
    service = ChartProposalService(InMemoryChartProposalRepository())
    note = _note()

    service.refresh(note, chart, PARSED, [])

    assert [p.field_key for p in service.proposals(note.id)] == ["alcohol"]


# --- The import route ---------------------------------------------------------------


class _Parse:
    def parse_note(self, source_text: str, definition: NoteTypeDefinition) -> ParsedImportedNote:
        content = PARSED if definition.key == FOLLOW_UP else {"subjective": {}}
        return ParsedImportedNote(content=content, session_date=None, session_time=None)


class _Proposing(MockNoteGenerationService):
    def __init__(self, reply: dict[str, Any]) -> None:
        super().__init__()
        self.reply = reply
        self.prompts: list[str] = []

    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(user)
            return self.reply

        return complete


@pytest.fixture
def imported(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> Iterator[tuple[str, InMemoryChartProposalRepository, _Proposing]]:
    now = datetime.now(UTC)
    patient = mock_repo.create(
        Patient(
            id=str(uuid.uuid4()),
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
    proposals = InMemoryChartProposalRepository()
    history = InMemoryChartHistoryRepository()
    generator = _Proposing(_reply(_work_school([P_INTERVAL, P_CARRIED])))
    overrides: dict[Any, Any] = {
        notes_routes.get_registry: lambda: registry,
        notes_routes.get_note_generation_service: lambda: generator,
        sessions_routes.get_note_import_service: _Parse,
        sessions_routes.get_session_service: lambda: SessionService(
            mock_session_repo, mock_repo, generator, NoteService(mock_notes_repo)
        ),
        get_chart_proposal_repository: lambda: proposals,
        get_chart_history_repository: lambda: history,
    }
    app.dependency_overrides.update(overrides)
    yield f"/api/patients/{patient.id}/sessions/import", proposals, generator
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)


def _import(client: TestClient, url: str, note_type: str) -> dict[str, Any]:
    response = client.post(
        url,
        files={"file": ("transfer.txt", DOCUMENT.encode(), "text/plain")},
        data={"note_type": note_type},
    )
    assert response.status_code == 201, response.text
    note: dict[str, Any] = response.json()["note"]
    return note


def test_an_imported_prescriber_note_proposes_from_its_document(
    client: TestClient, imported: tuple[str, InMemoryChartProposalRepository, _Proposing]
) -> None:
    url, proposals, generator = imported

    note = _import(client, url, FOLLOW_UP)

    listed = client.get(f"/api/notes/{note['id']}/chart-proposals").json()
    assert (listed["run"]["status"], listed["run"]["retryable"]) == ("ok", True)
    by_field = {p["field_key"]: p for p in listed["data"]}
    assert by_field["work_school"]["origin"] == "document"
    assert [e["text"] for e in by_field["work_school"]["evidence"]] == [CARRIED, INTERVAL]
    assert by_field["alcohol"]["what_changed"] == RECORDED_THIS_VISIT
    assert "Document (each paragraph numbered [Sn]):" in generator.prompts[0]
    # The proposals live beside the note, never in it.
    assert LAID_OFF not in json.dumps(note["content"])

    # Retry reads the document again from the session it was stored with.
    retried = client.post(f"/api/notes/{note['id']}/chart-proposals/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["run"]["status"] == "ok"
    assert len(generator.prompts) == 2
    assert f"[S{P_INTERVAL}] INTERVAL HISTORY" in generator.prompts[1]
    assert proposals.run(note["id"]) is not None


def test_a_soap_import_is_unchanged_and_proposes_nothing(
    client: TestClient, imported: tuple[str, InMemoryChartProposalRepository, _Proposing]
) -> None:
    url, proposals, generator = imported

    note = _import(client, url, "soap")

    assert generator.prompts == []
    assert proposals.run(note["id"]) is None
    assert proposals.list_for_note(note["id"]) == []
