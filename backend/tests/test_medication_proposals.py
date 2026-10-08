# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Medication changes a note proposes: drafted from the visit, applied on accept."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.drafting import RESPONSE_SCHEMA, build_prompt, parse_proposals
from app.chart_proposals.families import MEDICATIONS, ChartChangedError, ChartWriters
from app.chart_proposals.recorded import recorded_proposals
from app.chart_proposals.service import (
    ChartProposalService,
    Choice,
    ProposalNotEditableError,
)
from app.medications.repository import InMemoryMedicationRepository
from app.medications.schemas import CreateMedicationRequest
from app.medications.service import MedicationService
from app.models import Note, Patient
from app.notes.chart_context import ChartContext, chart_context_for
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientRepository,
)

if TYPE_CHECKING:
    from app.chart_proposals.models import DraftedProposal

USER = "clinician-1"
VISIT = date(2026, 10, 6)

SEGMENTS = {
    0: "[00:01] Clinician: Let's start buspirone 5 mg in the afternoon as needed.",
    1: "[00:09] Clinician: And stop the lithium, because of the nausea.",
    2: "[00:15] Client: My primary care doctor started me on amlodipine 5 mg every morning.",
    3: "[00:21] Clinician: Let's take the sertraline to every evening instead.",
}


def _rows(*meds: tuple[str, str, str | None]) -> list[dict[str, object]]:
    return [
        {"drug_name": name, "dose": dose, "frequency": frequency, "status": "active"}
        for name, dose, frequency in meds
    ]


def _chart(*meds: tuple[str, str, str | None]) -> ChartContext:
    now = datetime.now(UTC)
    patient = Patient(id="p", first_name="Sam", last_name="Sample", created_at=now, updated_at=now)
    return chart_context_for(patient, [], _rows(*meds))


LISTED = _chart(
    ("lithium", "300 mg", "twice daily"),
    ("sertraline", "100 mg", "every morning"),
)


def _item(action: str, name: str, ids: list[int], **fields: str) -> dict[str, Any]:
    return {
        "action": action,
        "drug_name": name,
        "what_changed": f"{action} {name}",
        "evidence_segment_ids": ids,
        **fields,
    }


def _parse(*items: dict[str, Any], chart: ChartContext = LISTED) -> list[DraftedProposal]:
    return parse_proposals({"proposals": [], "medication_changes": list(items)}, chart, SEGMENTS)


# --- The proposal call -------------------------------------------------------------


def test_a_start_and_a_stop_are_proposed_with_frequency_and_reason() -> None:
    kept = _parse(
        _item("start", "buspirone", [0], dose="5 mg", frequency="in the afternoon as needed"),
        _item("stop", "Lithium", [1], reason="nausea"),
    )

    start, stop = kept
    assert start.change is not None
    assert stop.change is not None
    assert (start.field_key, start.item_key, start.proposed_text) == (
        MEDICATIONS,
        "buspirone",
        "buspirone 5 mg, in the afternoon as needed",
    )
    assert (start.change.action, start.change.frequency) == ("start", "in the afternoon as needed")
    # A stop names the medication as the list does, and keeps the stated reason.
    assert (stop.item_key, stop.change.reason, stop.proposed_text) == (
        "lithium",
        "nausea",
        "Stopped: nausea",
    )
    assert [e.segment_id for e in stop.evidence] == [1]


def test_an_add_is_a_medication_the_client_takes_that_the_list_lacks() -> None:
    (add,) = _parse(_item("add", "amlodipine", [2], dose="5 mg", frequency="every morning"))

    assert add.change is not None
    assert (add.change.action, add.proposed_text) == ("add", "amlodipine 5 mg, every morning")


def test_a_change_keeps_whatever_it_does_not_change() -> None:
    (change,) = _parse(_item("change", "sertraline", [3], frequency="every evening"))

    assert change.change is not None
    assert change.change.dose is None
    assert change.proposed_text == "sertraline 100 mg, every evening"


@pytest.mark.parametrize(
    "item",
    [
        _item("start", "sertraline", [3], dose="100 mg"),
        _item("add", "Sertraline", [3]),
        _item("stop", "quetiapine", [1]),
        _item("change", "quetiapine", [3], dose="50 mg"),
        _item("change", "sertraline", [3], dose="100 mg", frequency="every morning"),
        _item("change", "sertraline", [3]),
        _item("stop", "lithium", []),
        _item("stop", "lithium", [9]),
        _item("taper", "lithium", [1]),
        _item("start", " ", [0]),
    ],
    ids=[
        "start-listed",
        "add-listed",
        "stop-unlisted",
        "change-unlisted",
        "change-to-the-same",
        "change-nothing",
        "no-evidence",
        "not-this-visit",
        "unknown-action",
        "no-name",
    ],
)
def test_a_change_the_list_cannot_take_or_the_visit_does_not_state_is_dropped(
    item: dict[str, Any],
) -> None:
    assert _parse(item) == []


def test_a_medication_in_the_text_proposals_is_dropped() -> None:
    reply = {
        "proposals": [
            {
                "field_key": MEDICATIONS,
                "entry": "buspirone",
                "proposed_text": "buspirone 5 mg",
                "what_changed": "Started",
                "evidence_segment_ids": [0],
            }
        ]
    }

    assert parse_proposals(reply, LISTED, SEGMENTS) == []


def test_the_call_asks_for_medication_changes_in_their_own_list() -> None:
    properties = RESPONSE_SCHEMA["properties"]
    text_keys = properties["proposals"]["items"]["properties"]["field_key"]["enum"]

    assert MEDICATIONS not in text_keys
    assert properties["medication_changes"]["items"]["properties"]["action"]["enum"] == [
        "start",
        "stop",
        "change",
        "add",
    ]


def test_the_prompt_lists_the_medications_and_reads_the_drafts_stated_suffix() -> None:
    draft = {
        "medications": {
            "current_medications": [
                "lithium 300 mg, twice daily",
                '(stated this visit: "amlodipine 5 mg every morning")',
            ]
        }
    }

    prompt = build_prompt(LISTED, "[S0] hello", draft=draft)

    assert "- lithium 300 mg, twice daily" in prompt
    assert "medication_changes" in prompt
    assert "without a decision from the clinician" in prompt
    assert '- medications: lithium 300 mg, twice daily\n(stated this visit: "amlodipine' in prompt
    assert "Medication list: none recorded" in build_prompt(ChartContext(), "[S0] hello")


def test_the_notes_own_text_never_seeds_a_medication() -> None:
    content = {
        "medications": {
            "current_medications": 'None recorded (stated this visit: "amlodipine 5 mg daily")'
        }
    }

    assert recorded_proposals(content, ()) == []


# --- Accepting -----------------------------------------------------------------------


@pytest.fixture
def parts() -> tuple[Patient, Note, MedicationService, ChartProposalService]:
    patients = InMemoryPatientRepository()
    now = datetime.now(UTC)
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
    )
    repo = InMemoryMedicationRepository()
    repo.grant_all_access()
    medications = MedicationService(repo)
    for name, dose, frequency in (
        ("lithium", "300 mg", "twice daily"),
        ("sertraline", "100 mg", "every morning"),
    ):
        medications.create(
            patient.id,
            USER,
            CreateMedicationRequest(drug_name=name, dose=dose, frequency=frequency),
        )
    service = ChartProposalService(
        InMemoryChartProposalRepository(),
        ChartWriters(
            history=ChartHistoryService(InMemoryChartHistoryRepository()),
            patients=patients,
            medications=medications,
        ),
        visit_date=VISIT,
    )
    return patient, note, medications, service


def _propose(
    parts: tuple[Patient, Note, MedicationService, ChartProposalService], *items: dict[str, Any]
) -> list[str]:
    patient, note, medications, service = parts
    chart = chart_context_for(patient, [], medications.list_by_patient(patient.id, USER))
    service.refresh(note, chart, {}, _parse(*items, chart=chart))
    return [p.id for p in service.proposals(note.id)]


def _by_name(medications: MedicationService, patient: Patient) -> dict[str, dict[str, object]]:
    return {str(r["drug_name"]): r for r in medications.list_by_patient(patient.id, USER)}


def test_accepting_a_start_and_a_stop_writes_the_list_from_the_note(parts: Any) -> None:
    patient, note, medications, service = parts
    start, stop = _propose(
        parts,
        _item(
            "start",
            "buspirone",
            [0],
            dose="5 mg",
            frequency="in the afternoon as needed",
            category="psychiatric",
        ),
        _item("stop", "lithium", [1], reason="nausea"),
    )

    service.decide(note, patient, start, Choice("accept"), USER)
    service.decide(note, patient, stop, Choice("accept"), USER)

    rows = _by_name(medications, patient)
    started, stopped = rows["buspirone"], rows["lithium"]
    assert (
        started["status"],
        started["dose"],
        started["frequency"],
        started["category"],
        started["started_at"],
        started["source_note_id"],
    ) == ("active", "5 mg", "in the afternoon as needed", "psychiatric", VISIT, note.id)
    # Stopped, never deleted.
    assert (
        stopped["status"],
        stopped["stopped_at"],
        stopped["stop_reason"],
        stopped["deleted_at"],
        stopped["source_note_id"],
    ) == ("discontinued", VISIT, "nausea", None, note.id)
    assert len(rows) == 3


def test_an_add_is_active_with_no_start_date_of_this_visit(parts: Any) -> None:
    patient, note, medications, service = parts
    (add,) = _propose(parts, _item("add", "amlodipine", [2], dose="5 mg", frequency="daily"))

    service.decide(note, patient, add, Choice("accept"), USER)

    added = _by_name(medications, patient)["amlodipine"]
    assert (added["status"], added["started_at"], added["frequency"]) == ("active", None, "daily")


def test_a_change_of_frequency_alone_keeps_the_dose_and_the_prior_values(parts: Any) -> None:
    patient, note, medications, service = parts
    (change,) = _propose(parts, _item("change", "sertraline", [3], frequency="every evening"))

    service.decide(note, patient, change, Choice("accept"), USER)

    row = _by_name(medications, patient)["sertraline"]
    assert (row["dose"], row["frequency"], row["status"]) == ("100 mg", "every evening", "active")
    assert row["notes"] == "Was 100 mg, every morning until 2026-10-06."
    assert row["source_note_id"] == note.id


def test_a_change_of_dose_alone_keeps_the_frequency(parts: Any) -> None:
    patient, note, medications, service = parts
    (change,) = _propose(parts, _item("change", "sertraline", [3], dose="150 mg"))

    service.decide(note, patient, change, Choice("accept"), USER)

    row = _by_name(medications, patient)["sertraline"]
    assert (row["dose"], row["frequency"]) == ("150 mg", "every morning")


def test_a_medication_change_is_accepted_or_discarded_never_rewritten(parts: Any) -> None:
    patient, note, medications, service = parts
    start, stop = _propose(
        parts,
        _item("start", "buspirone", [0], dose="5 mg"),
        _item("stop", "lithium", [1]),
    )

    with pytest.raises(ProposalNotEditableError):
        service.decide(note, patient, start, Choice("edit", "buspirone 10 mg"), USER)
    discarded = service.decide(note, patient, stop, Choice("discard"), USER)

    assert discarded.decision == "discarded"
    assert set(_by_name(medications, patient)) == {"lithium", "sertraline"}
    assert _by_name(medications, patient)["lithium"]["status"] == "active"


def test_a_stop_of_a_medication_stopped_since_leaves_the_proposal_pending(parts: Any) -> None:
    patient, note, medications, service = parts
    (stop,) = _propose(parts, _item("stop", "lithium", [1]))
    lithium = _by_name(medications, patient)["lithium"]
    medications.soft_delete(str(lithium["id"]), USER)

    with pytest.raises(ChartChangedError):
        service.decide(note, patient, stop, Choice("accept"), USER)

    (proposal,) = service.proposals(note.id)
    assert proposal.pending
