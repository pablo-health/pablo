# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""After an archive applies, each client's chart is proposed from their notes.

The most recent imported progress note and the treatment plan are read, the
way a single-document import's document is; a psychotherapy note never is.
What they say about the chart lands as pending proposals on the note that
said it, citing its paragraph; nothing lands on the chart. A failed call marks
the note and costs nothing else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_proposals.step import ChartProposalStep
from app.migration.chart_seed import ChartSeeder, SeedReport, notes_to_read
from app.models import Note, Patient
from app.repositories import InMemoryChartHistoryRepository, InMemoryChartProposalRepository
from app.services.note_generation_service import MockNoteGenerationService

USER = "test-user-123"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _source(kind: str, title: str, *, visit: str | None = None, created: str) -> dict[str, Any]:
    return {
        "system": "simplepractice",
        "source_id": str(uuid.uuid4().int)[:10],
        "kind": kind,
        "title": title,
        "created_at": created,
        "visit": {"starts_at": visit} if visit else None,
        "diagnoses": [],
    }


def _note(
    patient_id: str,
    body: str,
    source: dict[str, Any] | None,
    *,
    note_type: str = "narrative",
    edited: bool = False,
) -> Note:
    content = {"note": {"body": body}, **({"__source": source} if source else {})}
    return Note(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        note_type=note_type,
        created_at=NOW,
        updated_at=NOW,
        content=None if edited else content,
        content_edited=content if edited else None,
        restricted=edited,
    )


class _Answering(MockNoteGenerationService):
    """Answers the proposal call with one proposal per "Update on <key>: <text>" paragraph."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(user)
            proposals = []
            for line in user.splitlines():
                head, found, rest = line.partition("Update on ")
                if not found or not head.startswith("[S"):
                    continue
                segment = int(head[2:].partition("]")[0])
                key, _, text = rest.partition(": ")
                proposals.append(
                    {
                        "field_key": key,
                        "proposed_text": text,
                        "what_changed": "Stated in the note",
                        "evidence_segment_ids": [segment],
                    }
                )
            return {"proposals": proposals}

        return complete


class _Failing(MockNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            raise TimeoutError("model unavailable")

        return complete


class _Modelless(MockNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        return None


@pytest.fixture
def patient() -> Patient:
    return Patient(
        id=str(uuid.uuid4()),
        first_name="Lulu",
        last_name="Llama",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def repo() -> InMemoryChartProposalRepository:
    return InMemoryChartProposalRepository()


@pytest.fixture
def step(repo: InMemoryChartProposalRepository) -> ChartProposalStep:
    return ChartProposalStep(repo, InMemoryChartHistoryRepository())


def _lulus_notes(patient_id: str) -> dict[str, Note]:
    return {
        "older": _note(
            patient_id,
            "Client arrived on time.\n\nUpdate on supports: A sister.",
            _source(
                "progress",
                "Progress Note",
                visit="2026-09-24T13:00:00-04:00",
                created="2026-09-26T23:30:00-04:00",
            ),
        ),
        "latest": _note(
            patient_id,
            "Client reports a difficult week.\n\nUpdate on supports: Sister and a neighbor.",
            _source(
                "progress",
                "Progress Note",
                visit="2026-09-25T14:00:00-04:00",
                created="2026-09-26T23:30:00-04:00",
            ),
        ),
        "plan": _note(
            patient_id,
            "Diagnosis\n\nUpdate on psychotherapy_history: Weekly CBT since June.",
            _source(
                "treatment_plan",
                "Diagnosis and treatment plan",
                created="2026-09-26T23:30:00-04:00",
            ),
        ),
        "chart": _note(
            patient_id,
            "Client left a voicemail.",
            _source("chart", "Chart Note", created="2026-09-26T23:30:00-04:00"),
        ),
        "psychotherapy": _note(
            patient_id,
            "Update on supports: MUST NEVER BE READ.",
            _source(
                "psychotherapy",
                "Psychotherapy Note",
                visit="2026-09-25T14:00:00-04:00",
                created="2026-09-26T23:30:00-04:00",
            ),
            note_type="psychotherapy",
            edited=True,
        ),
        "written_here": _note(patient_id, "Update on supports: Written here.", None),
    }


def test_the_latest_progress_note_and_the_plan_are_read_and_nothing_else(
    patient: Patient,
) -> None:
    notes = _lulus_notes(patient.id)

    read = notes_to_read(notes.values())

    assert [n.id for n in read] == [notes["latest"].id, notes["plan"].id]


def test_what_the_notes_say_lands_as_pending_proposals_on_the_note_that_said_it(
    patient: Patient, step: ChartProposalStep, repo: InMemoryChartProposalRepository
) -> None:
    notes = _lulus_notes(patient.id)
    generator = _Answering()
    report = SeedReport()

    ChartSeeder(USER, step, generator).seed(patient, notes.values(), report)

    (supports,) = repo.list_for_note(notes["latest"].id)
    assert supports.field_key == "supports"
    assert supports.proposed_text == "Sister and a neighbor."
    assert supports.origin == "document"
    assert supports.pending
    assert [e.segment_id for e in supports.evidence] == [1]
    (therapy,) = repo.list_for_note(notes["plan"].id)
    assert therapy.field_key == "psychotherapy_history"
    assert repo.list_for_note(notes["older"].id) == []
    assert repo.list_for_note(notes["psychotherapy"].id) == []
    # The restricted note's text reached no prompt.
    assert all("MUST NEVER BE READ" not in prompt for prompt in generator.prompts)
    assert len(generator.prompts) == 2
    assert report.checked == 1
    assert report.clients == 1
    assert report.failed == 0
    assert report.patient_ids == [patient.id]
    run = repo.run(notes["latest"].id)
    assert run is not None
    assert run.status == "ok"


def test_a_failed_call_marks_the_note_and_counts_once(
    patient: Patient, step: ChartProposalStep, repo: InMemoryChartProposalRepository
) -> None:
    notes = _lulus_notes(patient.id)
    report = SeedReport()

    ChartSeeder(USER, step, _Failing()).seed(patient, notes.values(), report)

    assert report.checked == 1
    assert report.clients == 0
    assert report.failed == 2  # the progress note and the plan
    run = repo.run(notes["latest"].id)
    assert run is not None
    assert run.status == "failed"
    assert repo.list_for_note(notes["latest"].id) == []


def test_a_client_with_nothing_to_read_is_counted_and_skipped(
    patient: Patient, step: ChartProposalStep, repo: InMemoryChartProposalRepository
) -> None:
    only = _lulus_notes(patient.id)["chart"]
    report = SeedReport()

    ChartSeeder(USER, step, _Answering()).seed(patient, [only], report)

    assert report.nothing_to_read == 1
    assert report.checked == 0
    assert repo.list_for_note(only.id) == []


def test_no_model_means_no_proposals_and_no_run_marked(
    patient: Patient, step: ChartProposalStep, repo: InMemoryChartProposalRepository
) -> None:
    notes = _lulus_notes(patient.id)
    report = SeedReport()

    ChartSeeder(USER, step, _Modelless()).seed(patient, notes.values(), report)

    assert repo.list_for_note(notes["latest"].id) == []
    assert repo.run(notes["latest"].id) is None
    assert report.clients == 0
