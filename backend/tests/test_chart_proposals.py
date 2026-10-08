# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart proposals: the proposal call's checks, what a note's own text proposes, deciding."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.drafting import build_prompt, parse_proposals, propose_chart_updates
from app.chart_proposals.families import ChartWriters
from app.chart_proposals.models import RECORDED_THIS_VISIT, DraftedProposal
from app.chart_proposals.recorded import recorded_proposals, recorded_text, stated_history
from app.chart_proposals.service import (
    ChartProposalService,
    Choice,
    ProposalDecidedError,
)
from app.chart_proposals.step import ChartProposalStep
from app.models import Note, Patient, Transcript
from app.notes import get_default_registry
from app.notes.chart_context import ChartContext, ChartHistoryField
from app.notes.practice_types import PracticeNoteTypeSpec, practice_key, to_definition
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientRepository,
)
from app.services.note_generation_service import MockNoteGenerationService

if TYPE_CHECKING:
    from app.notes import NoteTypeDefinition

from app.notes.spec_templates import TEMPLATES_DIR as TEMPLATES

USER = "clinician-1"

TRANSCRIPT = Transcript(
    format="txt",
    content="\n".join(
        [
            "[00:01] Therapist: How have things been since we last met?",
            "[00:09] Client: The divorce was finalized on April 2.",
            "",
            "[00:15] Client: Otherwise the same. Still at the library, still with my sister.",
        ]
    ),
)
# Segments as the proposal call numbers them: blank lines are not segments.
DIVORCE = 1

SEPARATED = "Married; separated, divorce in progress since June."


def _definition(template: str) -> NoteTypeDefinition:
    spec = json.loads((TEMPLATES / f"{template}.json").read_text())["spec"]
    return to_definition(practice_key(template), 1, PracticeNoteTypeSpec.model_validate(spec))


def _chart(**history: str) -> ChartContext:
    return ChartContext(
        allergy_status="nkda",
        history=tuple(ChartHistoryField(k, v, date(2026, 6, 1)) for k, v in history.items()),
    )


def _reply(*proposals: dict[str, Any]) -> dict[str, Any]:
    return {"proposals": list(proposals)}


def _relationships(text: str, ids: list[Any]) -> dict[str, Any]:
    return {
        "field_key": "relationships",
        "proposed_text": text,
        "what_changed": "Divorce finalized",
        "evidence_segment_ids": ids,
    }


SEGMENTS = {0: "a", 1: "[00:09] Client: The divorce was finalized on April 2.", 2: "c"}
FINALIZED = "Married; separated June; divorce finalized April 2."


# --- The proposal call -------------------------------------------------------------


def test_a_proposal_citing_this_transcript_keeps_the_cited_lines() -> None:
    kept = parse_proposals(
        _reply(_relationships(FINALIZED, [DIVORCE, DIVORCE])),
        _chart(relationships=SEPARATED),
        SEGMENTS,
    )

    assert [(p.field_key, p.proposed_text) for p in kept] == [("relationships", FINALIZED)]
    assert [(e.segment_id, e.text) for e in kept[0].evidence] == [(DIVORCE, SEGMENTS[DIVORCE])]


@pytest.mark.parametrize(
    "ids",
    [[], [7], [DIVORCE, 7], [-1], ["1"], [True], None],
    ids=["none", "not-in-visit", "one-bad-of-two", "negative", "string", "bool", "missing"],
)
def test_a_proposal_whose_evidence_is_not_this_visits_is_dropped(ids: Any) -> None:
    assert parse_proposals(_reply(_relationships(FINALIZED, ids)), _chart(), SEGMENTS) == []


def test_nothing_changed_proposes_nothing() -> None:
    chart = _chart(relationships=SEPARATED)

    assert parse_proposals(_reply(), chart, SEGMENTS) == []
    # The chart's own text again is not a change.
    assert parse_proposals(_reply(_relationships(SEPARATED, [DIVORCE])), chart, SEGMENTS) == []


def test_a_proposal_never_empties_a_field() -> None:
    chart = _chart(relationships=SEPARATED)
    assert parse_proposals(_reply(_relationships("  ", [DIVORCE])), chart, SEGMENTS) == []


def test_an_unknown_field_is_dropped() -> None:
    unknown = {**_relationships(FINALIZED, [DIVORCE]), "field_key": "favourite_colour"}
    assert parse_proposals(_reply(unknown), _chart(), SEGMENTS) == []


def test_an_allergy_must_name_its_substance() -> None:
    allergy = {
        "field_key": "allergies",
        "proposed_text": "rash",
        "what_changed": "New allergy",
        "evidence_segment_ids": [DIVORCE],
    }
    assert parse_proposals(_reply(allergy), _chart(), SEGMENTS) == []
    kept = parse_proposals(_reply({**allergy, "entry": "Sulfa"}), _chart(), SEGMENTS)
    assert [(p.field_key, p.item_key, p.proposed_text) for p in kept] == [
        ("allergies", "Sulfa", "rash")
    ]


def test_the_call_numbers_the_transcript_and_a_failure_proposes_nothing() -> None:
    prompts: list[str] = []

    def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        return _reply(_relationships(FINALIZED, [DIVORCE]))

    kept = propose_chart_updates(complete, _chart(relationships=SEPARATED), TRANSCRIPT).proposals

    assert [p.evidence[0].text for p in kept] == [
        "[00:09] Client: The divorce was finalized on April 2."
    ]
    assert "[S2] [00:15] Client: Otherwise the same." in prompts[0]

    def fails(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model unavailable")

    failed = propose_chart_updates(fails, _chart(), TRANSCRIPT)
    assert (failed.proposals, failed.error_class) == ([], "RuntimeError")


def test_the_prompt_lists_every_field_and_says_never_to_remove() -> None:
    prompt = build_prompt(_chart(relationships=SEPARATED), "[S0] hello")

    assert f"- relationships (Social history and supports: Relationships): {SEPARATED}" in prompt
    assert "- trauma_history (Trauma history): (empty)" in prompt
    assert "- tobacco_nicotine (Substance use: Tobacco / nicotine): (empty)" in prompt
    assert "Allergies: No known drug allergies (NKDA)" in prompt
    assert "never remove a statement" in prompt
    assert "Never propose removing an allergy" in prompt
    assert "If nothing changed, return an empty list" in prompt


@pytest.mark.parametrize("person", ["client", "patient"])
def test_the_prompt_names_the_person_in_the_clinicians_word_and_never_genders_them(
    person: str,
) -> None:
    prompt = build_prompt(replace(_chart(), person=person), "[S0] hello")

    assert (
        f'Refer to the person seen as "the {person}" or with they/them; never he, she, his or '
        "her, unless the chart records their pronouns."
    ) in prompt
    assert "{term}" not in prompt


def test_a_field_the_draft_marks_stated_this_visit_is_named_in_the_prompt() -> None:
    draft = {
        "social_history": {"relationships": f"{SEPARATED} (stated this visit: finalized)"},
        "substance_use": {"nicotine": "Not asked.", "alcohol": "asked — no change"},
    }
    prompt = build_prompt(_chart(), "[S0] hello", draft=draft)

    assert f"- relationships: {SEPARATED} (stated this visit: finalized)" in prompt
    assert "- alcohol:" not in prompt


# --- What a note's own text proposes --------------------------------------------------


@pytest.mark.parametrize("key", ["tobacco_nicotine", "nicotine"])
def test_a_note_proposes_each_stated_field_the_chart_lacks_as_written(key: str) -> None:
    content = {
        "social_history": {"relationships": "Married 19 years.", "work_school": "Not stated."},
        "substance_use": {key: "Vapes daily.", "cocaine": "Not asked."},
        "psychiatric_history": {"hospitalizations": ["None.", ""]},
        "assessment": {"formulation": "Not a history field."},
    }

    seeds = recorded_proposals(content, recorded_keys=["hospitalizations"])

    assert [(p.field_key, p.proposed_text) for p in seeds] == [
        ("relationships", "Married 19 years."),
        ("tobacco_nicotine", "Vapes daily."),
    ]
    assert {(p.what_changed, p.evidence, p.origin) for p in seeds} == {
        (RECORDED_THIS_VISIT, (), "note")
    }
    assert stated_history(content)["hospitalizations"] == "None."


@pytest.mark.parametrize(
    ("text", "recorded"),
    [
        ("Not recorded", ""),
        ("Not recorded.", ""),
        ("None recorded", ""),
        ("asked — no change", ""),
        ("Not recorded (not asked this visit)", ""),
        ("Not recorded (asked this visit: no change)", ""),
        (
            "Not recorded (stated this visit: lives with a partner since May)",
            "lives with a partner since May",
        ),
        ('Not recorded (stated this visit: "a few times a month.")', "a few times a month."),
        ("Married 19 years.", "Married 19 years."),
    ],
)
def test_a_chart_fed_field_records_only_what_the_visit_stated(text: str, recorded: str) -> None:
    """A field the chart was empty for proposes what the visit stated, and nothing else."""
    assert recorded_text(text) == recorded


# --- Storing and deciding ---------------------------------------------------------------


def _patient(repo: InMemoryPatientRepository) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    patient.allergy_status = "recorded"
    patient.allergies = [{"substance": "codeine", "reaction": "nausea"}]
    return repo.create(patient, USER)


def _note(patient: Patient, content: dict[str, Any] | None = None) -> Note:
    now = datetime.now(UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=patient.id,
        note_type="custom.psychiatric_follow_up",
        created_at=now,
        updated_at=now,
        content=content,
    )


def _drafted(field_key: str, text: str, item_key: str = "") -> DraftedProposal:
    return DraftedProposal(
        field_key=field_key,
        proposed_text=text,
        what_changed="Changed",
        evidence=(),
        item_key=item_key,
    )


@pytest.fixture
def chart_parts() -> tuple[InMemoryPatientRepository, ChartHistoryService, ChartProposalService]:
    patients = InMemoryPatientRepository()
    history = ChartHistoryService(InMemoryChartHistoryRepository())
    proposals = ChartProposalService(
        InMemoryChartProposalRepository(), ChartWriters(history=history, patients=patients)
    )
    return patients, history, proposals


def test_accept_writes_the_chart_from_the_note_and_keeps_the_prior_text(chart_parts: Any) -> None:
    patients, history, service = chart_parts
    patient = _patient(patients)
    history.set(patient.id, "relationships", USER, SEPARATED)
    note = _note(patient)
    service.refresh(note, ChartContext(), {}, [_drafted("relationships", FINALIZED)])
    (proposal,) = service.proposals(note.id)

    decided = service.decide(note, patient, proposal.id, Choice("accept"), "signer-2")

    entry = history.entries(patient.id)["relationships"]
    assert (entry.text, entry.source_note_id, entry.updated_by) == (FINALIZED, note.id, "signer-2")
    assert [r.text for r in history.revisions(patient.id)] == [SEPARATED]
    assert (decided.decision, decided.decided_by, decided.decided_text) == (
        "accepted",
        "signer-2",
        None,
    )
    with pytest.raises(ProposalDecidedError):
        service.decide(note, patient, proposal.id, Choice("discard"), USER)


def test_edit_records_the_clinicians_text_and_discard_writes_nothing(chart_parts: Any) -> None:
    patients, history, service = chart_parts
    patient = _patient(patients)
    note = _note(patient)
    service.refresh(
        note,
        ChartContext(),
        {},
        [_drafted("work_school", "Laid off."), _drafted("supports", "Sister.")],
    )
    work, supports = service.proposals(note.id)

    service.decide(note, patient, work.id, Choice("edit", " Laid off in March. "), USER)
    service.decide(note, patient, supports.id, Choice("discard"), USER)

    assert history.entries(patient.id)["work_school"].text == "Laid off in March."
    assert "supports" not in history.entries(patient.id)
    assert [(p.decision, p.decided_text) for p in service.proposals(note.id)] == [
        ("edited", "Laid off in March."),
        ("discarded", None),
    ]


def test_an_allergy_is_only_ever_added_to(chart_parts: Any) -> None:
    patients, _, service = chart_parts
    patient = _patient(patients)
    note = _note(patient)
    service.refresh(
        note,
        ChartContext(),
        {},
        [
            _drafted("allergies", "Took it in February without a reaction.", item_key="Codeine"),
            _drafted("allergies", "Rash.", item_key="Sulfa"),
        ],
    )
    for proposal in service.proposals(note.id):
        service.decide(note, patient, proposal.id, Choice("accept"), USER)

    stored = patients.get(patient.id, USER)
    assert stored is not None
    assert stored.allergy_status == "recorded"
    assert stored.allergies == [
        {
            "substance": "codeine",
            "reaction": "nausea",
            "note": "Took it in February without a reaction.",
            "source_note_id": note.id,
        },
        {"substance": "Sulfa", "reaction": "Rash.", "source_note_id": note.id},
    ]


def test_a_redraft_replaces_what_is_pending_and_never_re_offers_a_decision(
    chart_parts: Any,
) -> None:
    patients, _, service = chart_parts
    patient = _patient(patients)
    note = _note(patient)
    service.refresh(
        note,
        ChartContext(),
        {},
        [_drafted("supports", "Sister."), _drafted("work_school", "Laid off.")],
    )
    supports = service.proposals(note.id)[0]
    service.decide(note, patient, supports.id, Choice("discard"), USER)

    service.refresh(
        note,
        ChartContext(),
        {},
        [_drafted("supports", "Sister and a friend."), _drafted("alcohol", "Stopped.")],
    )

    assert [(p.field_key, p.proposed_text, p.decision) for p in service.proposals(note.id)] == [
        ("supports", "Sister.", "discarded"),
        ("alcohol", "Stopped.", "pending"),
    ]


def test_the_notes_own_proposals_follow_its_text_until_decided(chart_parts: Any) -> None:
    patients, _, service = chart_parts
    patient = _patient(patients)
    content = {"social_history": {"relationships": "Married.", "supports": "Sister."}}
    note = _note(patient, content)
    drafted = [_drafted("supports", "Sister and a friend."), _drafted("alcohol", "Stopped.")]

    service.refresh(note, _chart(), content, drafted)
    # The note's own text wins over a drafted proposal for the same empty field.
    assert {(p.field_key, p.proposed_text, p.origin) for p in service.proposals(note.id)} == {
        ("relationships", "Married.", "note"),
        ("supports", "Sister.", "note"),
        ("alcohol", "Stopped.", "transcript"),
    }
    relationships = next(p for p in service.proposals(note.id) if p.field_key == "relationships")
    service.decide(note, patient, relationships.id, Choice("discard"), USER)

    # An edit is saved: the note's own proposals follow it; the drafted one stays.
    edited = {
        "social_history": {"relationships": "Married 19 years.", "living_situation": "Alone."}
    }
    service.refresh(note, _chart(), edited)

    assert {(p.field_key, p.proposed_text, p.decision) for p in service.proposals(note.id)} == {
        ("relationships", "Married.", "discarded"),
        ("living_situation", "Alone.", "pending"),
        ("alcohol", "Stopped.", "pending"),
    }


def test_the_visits_own_fields_and_the_review_only_mdm_propose_nothing() -> None:
    """Only a field keyed as a chart field is recorded: the HPI domains and the
    medical decision making are the visit's, never the chart's."""
    content = {
        "subjective": {"depression": "Low mood most days.", "insomnia_sleep": "Wakes at 3."},
        "mdm": {
            "problems_addressed": "One chronic illness with exacerbation.",
            "data_reviewed": "Not stated.",
            "management_risk": "Prescription drug management.",
        },
        "plan": {"education_provided": "Sleep hygiene."},
    }
    assert recorded_proposals(content, recorded_keys=[]) == []


def test_a_field_the_chart_already_has_proposes_nothing_of_its_own(chart_parts: Any) -> None:
    """A follow-up's history field prints the chart; only a drafted change is proposed."""
    patients, _, service = chart_parts
    patient = _patient(patients)
    content = {"social_history": {"relationships": SEPARATED, "supports": "Not recorded"}}
    note = _note(patient, content)

    service.refresh(note, _chart(relationships=SEPARATED), content)

    assert service.proposals(note.id) == []


# --- The step after a draft -----------------------------------------------------------------


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


def test_the_step_proposes_only_for_a_practice_type() -> None:
    generator = _Proposing(_reply(_relationships(FINALIZED, [DIVORCE])))
    step = ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())
    chart = _chart(relationships=SEPARATED)

    follow_up = step.draft(generator, _definition("psychiatric_follow_up"), chart, TRANSCRIPT, {})
    assert follow_up is not None
    assert [p.field_key for p in follow_up.proposals] == ["relationships"]

    assert step.draft(generator, get_default_registry().get("soap"), chart, TRANSCRIPT, {}) is None
    assert (
        step.draft(
            MockNoteGenerationService(), _definition("psychiatric_follow_up"), chart, TRANSCRIPT, {}
        )
        is None
    )
