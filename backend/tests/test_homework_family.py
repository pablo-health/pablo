# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework suggestions as a chart-proposal family.

What this catches: a homework suggestion leaking into the note proposal
call's prompt or reply schema (it is read from the signed note, never
drafted); a redraft or a saved edit of the note deleting suggestions that
are still waiting (``refresh`` replaces only the origins it computes);
accepting or editing a suggestion that does not become an assignment, or
becomes one with the wrong words or no link back to the suggestion; and a
dismissed one being turned into an assignment anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.families import (
    HOMEWORK,
    ChartWriters,
    HomeworkFamily,
    family_for,
    proposable_keys,
)
from app.chart_proposals.models import ChartProposal, DraftedProposal
from app.chart_proposals.service import ChartProposalService, Choice
from app.models import Note, Patient
from app.notes.chart_context import ChartContext
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientRepository,
)

USER = "clinician-1"


@dataclass
class _Writer:
    """Records what an accepted suggestion asks to assign."""

    assigned: list[dict[str, Any]] = field(default_factory=list)

    def assign_suggestion(self, patient_id: str, **kwargs: Any) -> None:
        self.assigned.append({"patient_id": patient_id, **kwargs})


def _parts() -> tuple[
    Patient, Note, ChartProposalService, InMemoryChartProposalRepository, _Writer
]:
    now = datetime.now(UTC)
    patients = InMemoryPatientRepository()
    patient = patients.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Sam",
            last_name="Sample",
            created_at=now,
            updated_at=now,
        ),
        USER,
    )
    note = Note(
        id=str(uuid.uuid4()),
        patient_id=patient.id,
        note_type="custom.psychiatric_follow_up",
        created_at=now,
        updated_at=now,
        content={},
    )
    repo = InMemoryChartProposalRepository()
    writer = _Writer()
    writers = ChartWriters(
        history=ChartHistoryService(InMemoryChartHistoryRepository()),
        patients=patients,
        homework=writer,
    )
    return patient, note, ChartProposalService(repo, writers), repo, writer


def _suggestion(note: Note, item_key: str, text: str) -> ChartProposal:
    return ChartProposal(
        id=str(uuid.uuid4()),
        note_id=note.id,
        patient_id=note.patient_id,
        field_key=HOMEWORK,
        item_key=item_key,
        proposed_text=text,
        what_changed="In the plan: “Client will keep a sleep diary.”",
        evidence=(),
        origin="homework",
        created_at=datetime.now(UTC),
    )


def test_the_family_is_decided_like_any_chart_update_and_never_drafted() -> None:
    assert isinstance(family_for(HOMEWORK), HomeworkFamily)
    assert HOMEWORK not in proposable_keys(), "never in the proposal call's reply schema"
    family = HomeworkFamily()
    assert family.chart_lines(ChartContext()) == []
    assert family.rules() == []
    assert family.editable


def test_a_redraft_or_saved_edit_leaves_waiting_suggestions_alone() -> None:
    _, note, service, repo, _ = _parts()
    repo.add([_suggestion(note, "plan.lifestyle_counseling#0", "Keep a sleep diary")])
    drafted = DraftedProposal(
        field_key="relationships", proposed_text="Married.", what_changed="x", evidence=()
    )
    service.refresh(note, ChartContext(), {}, [drafted])
    service.refresh(note, ChartContext(), {})
    keys = {p.field_key for p in service.proposals(note.id)}
    assert keys == {HOMEWORK, "relationships"}


def test_accepting_or_editing_assigns_it_with_a_link_back() -> None:
    patient, note, service, repo, writer = _parts()
    first = _suggestion(note, "plan.lifestyle_counseling#0", "Keep a sleep diary")
    second = _suggestion(note, "psychotherapy.goal_plan#0", "Log panic episodes")
    repo.add([first, second])

    service.decide(note, patient, first.id, Choice("accept"), USER)
    service.decide(note, patient, second.id, Choice("edit", "  Log each panic episode  "), USER)

    assert writer.assigned == [
        {
            "patient_id": patient.id,
            "text": "Keep a sleep diary",
            "proposed_text": "Keep a sleep diary",
            "note_id": note.id,
            "proposal_id": first.id,
            "source_field": "plan.lifestyle_counseling",
            "user_id": USER,
        },
        {
            "patient_id": patient.id,
            "text": "Log each panic episode",
            "proposed_text": "Log panic episodes",
            "note_id": note.id,
            "proposal_id": second.id,
            "source_field": "psychotherapy.goal_plan",
            "user_id": USER,
        },
    ]


def test_dismissing_assigns_nothing_and_an_overlong_edit_is_refused() -> None:
    patient, note, service, repo, writer = _parts()
    dismissed = _suggestion(note, "plan.homework_assignments#0", "Read chapter 2")
    long = _suggestion(note, "plan.homework_assignments#1", "Walk")
    repo.add([dismissed, long])
    service.decide(note, patient, dismissed.id, Choice("discard"), USER)
    with pytest.raises(ValueError, match="longer"):
        service.decide(note, patient, long.id, Choice("edit", "x" * 301), USER)
    assert writer.assigned == []
    assert {p.id: p.decision for p in service.proposals(note.id)}[long.id] == "pending"
