# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Seeding the chart from a note's own text, for both shapes a field can take.

An evaluation drafts its substance fields from the visit (an answer,
"Denies.", "Not asked."); a follow-up prints the chart's baseline and marks
the screen. One rule reads both, and an allergy denial seeds no known drug
allergies, never an allergy entry.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_history.fields import SUBSTANCE_KEYS
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.denials import NKDA, NKDA_TEXT, is_allergy_denial
from app.chart_proposals.drafting import parse_proposals
from app.chart_proposals.families import ChartChangedError, ChartWriters
from app.chart_proposals.recorded import recorded_proposals, recorded_text
from app.chart_proposals.service import ChartProposalService, Choice
from app.models import Note, Patient
from app.notes.chart_context import ChartContext, render_chart_block
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientRepository,
)

USER = "clinician-1"
SEGMENTS = {0: "[00:01] Therapist: Any allergies?", 1: "[00:03] Client: None that I know of."}

# Episode 06's intake: one question about four substances, one "No. None of that."
INTAKE_SUBSTANCES = {
    "alcohol": "Three or four beers on Friday nights with coworkers; none on weekdays.",
    "cannabis": "Not asked.",
    "stimulants": "Denies.",
    "cocaine": "Denies.",
    "opioids": "Denies.",
    "benzodiazepines": "Denies.",
    "tobacco_nicotine": "Denies smoking or vaping.",
    "other_substances": "Not asked.",
}
INTAKE_ALLERGY_DENIAL = 'Not recorded (stated this visit: "No. None that I know of.")'


@pytest.mark.parametrize(
    ("text", "recorded"),
    [
        # The evaluation's shape: the visit's answer, as written.
        ("Denies.", "Denies."),
        ("Denies smoking or vaping.", "Denies smoking or vaping."),
        ("A glass of wine on weekends.", "A glass of wine on weekends."),
        ("Not asked.", ""),
        ("Not asked", ""),
        # The follow-up's shape, on a chart with no baseline.
        ("Not recorded (stated this visit: denied)", "Denies."),
        ('Not recorded (stated this visit: "denied")', "Denies."),
        ('Not recorded (stated this visit: "vapes since June")', "vapes since June"),
        ("Not recorded (asked this visit: no change)", ""),
        ("Not recorded (not asked this visit)", ""),
        # A mark neither shape writes states nothing: the rule does not guess.
        ("Not recorded (not asked this visit: asked this visit). Client denied.", ""),
    ],
)
def test_each_substance_shape_records_what_the_visit_stated(text: str, recorded: str) -> None:
    assert recorded_text(text) == recorded


def test_four_denials_in_one_answer_seed_four_baselines() -> None:
    seeds = recorded_proposals({"substance_use": INTAKE_SUBSTANCES}, recorded_keys=[])

    assert {p.field_key: p.proposed_text for p in seeds} == {
        "alcohol": INTAKE_SUBSTANCES["alcohol"],
        "stimulants": "Denies.",
        "cocaine": "Denies.",
        "opioids": "Denies.",
        "benzodiazepines": "Denies.",
        "tobacco_nicotine": "Denies smoking or vaping.",
    }
    assert {p.origin for p in seeds} == {"note"}


def test_both_shapes_seed_by_the_same_rule_without_a_note_type() -> None:
    """A follow-up's denial mark and an evaluation's denial seed the same baseline,
    and a substance the chart already records is never seeded over."""
    follow_up = {
        "substance_use": {
            "alcohol": "Two beers a week. (asked this visit: no change)",
            "cocaine": "Not recorded (stated this visit: denied)",
            "opioids": "Not recorded (not asked this visit)",
        }
    }
    evaluation = {"substance_use": {"alcohol": "Two beers a week.", "cocaine": "Denies."}}

    def seeded(content: dict[str, Any]) -> dict[str, str]:
        return {
            p.field_key: p.proposed_text
            for p in recorded_proposals(content, recorded_keys=["alcohol"])
        }

    assert seeded(follow_up) == seeded(evaluation) == {"cocaine": "Denies."}


@pytest.mark.parametrize(
    "text",
    [
        "No. None that I know of.",
        "None.",
        "No known drug allergies.",
        "No known medication allergies per client's report.",
        "Denies.",
        "NKDA",
        "I'm not allergic to any medications.",
    ],
)
def test_a_statement_of_no_allergies_is_a_denial(text: str) -> None:
    assert is_allergy_denial(text)


@pytest.mark.parametrize(
    "text",
    [
        "Penicillin. I get a rash.",
        "No, but sulfa gives me hives.",
        "No drug allergies; allergic to peanuts.",
        "Took codeine since without a reaction.",
        "",
    ],
)
def test_a_statement_that_names_something_is_not_a_denial(text: str) -> None:
    assert not is_allergy_denial(text)


def test_an_allergy_denial_at_intake_seeds_nkda_and_never_an_entry() -> None:
    content = {"medications": {"allergies": INTAKE_ALLERGY_DENIAL}}

    (seed,) = recorded_proposals(content, recorded_keys=[], allergy_status="not_recorded")

    assert (seed.field_key, seed.item_key, seed.proposed_text, seed.origin) == (
        "allergies",
        NKDA,
        NKDA_TEXT,
        "note",
    )
    # The chart's allergy record already says something: nothing to seed.
    for status in ("nkda", "recorded"):
        assert recorded_proposals(content, recorded_keys=[], allergy_status=status) == []


def test_a_stated_allergy_is_not_seeded_from_the_note() -> None:
    """An entry needs its substance; the proposal call names it."""
    content = {"medications": {"allergies": 'Not recorded (stated this visit: "Penicillin, rash")'}}
    assert recorded_proposals(content, recorded_keys=[], allergy_status="not_recorded") == []


def _allergy(entry: str, text: str) -> dict[str, Any]:
    return {
        "field_key": "allergies",
        "entry": entry,
        "proposed_text": text,
        "what_changed": "Stated this visit",
        "evidence_segment_ids": [1],
    }


@pytest.mark.parametrize(
    ("entry", "text"),
    [
        ("No known allergies", "No known medication allergies."),
        ("medications", "No known medication allergies per client's report."),
        ("None", "Client denies allergies."),
    ],
)
def test_the_proposal_calls_denial_is_never_an_allergy_entry(entry: str, text: str) -> None:
    assert parse_proposals({"proposals": [_allergy(entry, text)]}, ChartContext(), SEGMENTS) == []


def test_a_stated_allergy_is_proposed_as_an_entry_with_its_reaction() -> None:
    kept = parse_proposals(
        {"proposals": [_allergy("Penicillin", "Rash.")]}, ChartContext(), SEGMENTS
    )
    assert [(p.item_key, p.proposed_text) for p in kept] == [("Penicillin", "Rash.")]


def _parts() -> tuple[InMemoryPatientRepository, ChartHistoryService, ChartProposalService]:
    patients = InMemoryPatientRepository()
    history = ChartHistoryService(InMemoryChartHistoryRepository())
    service = ChartProposalService(
        InMemoryChartProposalRepository(), ChartWriters(history=history, patients=patients)
    )
    return patients, history, service


def _patient(patients: InMemoryPatientRepository) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    return patients.create(patient, USER)


def _intake(patient: Patient, content: dict[str, Any]) -> Note:
    now = datetime.now(UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=patient.id,
        note_type="custom.psychiatric_evaluation",
        created_at=now,
        updated_at=now,
        content=content,
    )


def test_accepting_the_intakes_seeds_fills_the_baseline_and_sets_nkda() -> None:
    patients, history, service = _parts()
    patient = _patient(patients)
    content = {
        "substance_use": INTAKE_SUBSTANCES,
        "medications": {"allergies": INTAKE_ALLERGY_DENIAL},
    }
    note = _intake(patient, content)
    # The proposal call's denial-as-entry is dropped before it reaches the service.
    drafted = parse_proposals(
        {"proposals": [_allergy("No known allergies", "No known allergies.")]},
        ChartContext(),
        SEGMENTS,
    )
    service.refresh(note, ChartContext(), content, drafted)

    for proposal in service.proposals(note.id):
        service.decide(note, patient, proposal.id, Choice("accept"), USER)

    stored = patients.get(patient.id, USER)
    assert stored is not None
    assert (stored.allergy_status, stored.allergies) == ("nkda", [])
    entries = history.entries(patient.id)
    assert {k: entries[k].text for k in SUBSTANCE_KEYS if k in entries} == {
        "alcohol": INTAKE_SUBSTANCES["alcohol"],
        "stimulants": "Denies.",
        "cocaine": "Denies.",
        "opioids": "Denies.",
        "benzodiazepines": "Denies.",
        "tobacco_nicotine": "Denies smoking or vaping.",
    }


def test_nkda_is_not_written_over_an_allergy_listed_since() -> None:
    patients, _, service = _parts()
    patient = _patient(patients)
    content = {"medications": {"allergies": INTAKE_ALLERGY_DENIAL}}
    note = _intake(patient, content)
    service.refresh(note, ChartContext(), content, [])
    (proposal,) = service.proposals(note.id)
    patient.allergies = [{"substance": "Sulfa", "reaction": "hives"}]
    patient.allergy_status = "recorded"
    patients.update(patient)

    with pytest.raises(ChartChangedError):
        service.decide(note, patient, proposal.id, Choice("accept"), USER)

    stored = patients.get(patient.id, USER)
    assert stored is not None
    assert stored.allergy_status == "recorded"


# --- The two shapes stay distinct in the templates ---------------------------------

_FOLLOW_UP_MARKS = ("this visit", "not recorded", "no change", "baseline")


def _substance_hints(template: str) -> dict[str, str]:
    spec = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    return {
        field["key"]: field["ai_hint"]
        for section in spec["sections"]
        for field in section["fields"]
        if field["key"] in SUBSTANCE_KEYS
    }


def test_the_evaluations_substance_hints_ask_for_the_answer_and_never_a_screen_mark() -> None:
    hints = _substance_hints("psychiatric_evaluation")

    assert set(hints) == set(SUBSTANCE_KEYS)
    for key, hint in hints.items():
        assert not [m for m in _FOLLOW_UP_MARKS if m in hint.lower()], key
        assert '"Denies."' in hint, key
        assert '"Not asked."' in hint, key
        assert "drafted from the visit, not the chart" in hint, key


def test_the_follow_ups_substance_hints_keep_the_chart_shape() -> None:
    hints = _substance_hints("psychiatric_follow_up")
    assert hints
    for key, hint in hints.items():
        assert "(not asked this visit)" in hint, key


def test_the_chart_rule_covers_only_a_substance_field_fed_from_the_chart() -> None:
    block = " ".join(render_chart_block(ChartContext(), full_chart=True).split())
    assert "A substance-use field whose instructions say it comes from the chart prints" in block
