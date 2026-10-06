# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Derive on notes shaped like real ones: headers, headings, a rationale block.

All synthetic: written for this file, about no one.
"""

from __future__ import annotations

import re
from typing import Any

from app.notes.practice_types import PracticeNoteTypeSpec
from app.services.note_import_service import NoteImportService
from app.services.note_type_derive_checks import (
    SampleText,
    copied_paths,
    split_passages,
    unplaced_passages,
)
from app.services.note_type_derive_service import NoteTypeDeriveService
from app.services.note_type_derive_shape import strip_non_note
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

POSITIONAL = re.compile(r"^(?:field|section|input)_\d+$")

RATIONALE = """\
How the CPT Codes Were Selected (not part of your note)
The visit met moderate complexity because two chronic conditions were reviewed.
Psychotherapy minutes were counted separately from the medication visit.
"""

SAMPLE = f"""\
Client: Quill Harbinger    DOB: 01/02/1911
Date of Service: 03/04/2026    Start Time: 10:00 AM    End Time: 10:45 AM
CPT: 99214, 90836
Place of Service: 10 (Telehealth, client home)
Client Location: Faketown, AA    Provider Location: office in Sampleville, AA
Client consented to telehealth by video and identity was verified.

Chief Complaint
"My mornings feel like walking through wet cement."

History of Present Illness
Quill reports improved sleep since the dose change and fewer evening worries.

Mental Status Exam
Appearance: casually dressed. Mood "steady". Affect: reactive. Thought Process: linear.
The Mental Status Exam was otherwise unremarkable.

Risk Assessment
Denies suicidal ideation. Risk Assessment completed; low acute risk.

Plan
Continue current dose. Return in four weeks.

{RATIONALE}"""


def _field(key: str, label: str, hint: str) -> dict[str, Any]:
    return {"key": key, "label": label, "kind": "text", "ai_hint": hint}


PROPOSAL: dict[str, Any] = {
    "label": "Medication follow-up with psychotherapy",
    "description": "A follow-up note for medication management with therapy time.",
    "system_prompt": "Write concise clinical prose in the third person.",
    "sections": [
        {
            "key": "chief_complaint",
            "label": "Chief Complaint",
            "fields": [_field("chief_complaint", "Chief Complaint", "The concern in brief.")],
        },
        {
            "key": "history_of_present_illness",
            "label": "History of Present Illness",
            "fields": [_field("hpi", "History of Present Illness", "Changes since last visit.")],
        },
        {
            "key": "mental_status_exam",
            "label": "Mental Status Exam",
            "fields": [
                _field("appearance", "Appearance", "Dress and grooming."),
                _field("mood_affect", "Mood and Affect", "Stated mood; observed affect."),
                _field("thought_process", "Thought Process", "Organization of thought."),
            ],
        },
        {
            "key": "risk_assessment",
            "label": "Risk Assessment",
            "fields": [_field("risk_assessment", "Risk Assessment", "Suicidal ideation, risk.")],
        },
        {
            "key": "plan",
            "label": "Plan",
            "fields": [_field("plan", "Plan", "Medication plan and return visit.")],
        },
        {
            "key": "billing_justification",
            "label": "Billing Justification",
            "fields": [_field("decision_making", "Decision Making", "Why the level was chosen.")],
        },
    ],
    "inputs": [],
}


def _service(*replies: dict[str, Any] | Exception) -> tuple[NoteTypeDeriveService, Any]:
    gateway = FakeStructuredLLMGateway(
        responses=[
            r if isinstance(r, Exception) else StructuredCompletion(data=r) for r in replies
        ],
        default_response=StructuredCompletion(data={}),
    )
    service = NoteTypeDeriveService(
        NoteImportService(llm_gateway=gateway, model="m"), llm_gateway=gateway, model="m"
    )
    return service, gateway


def _keys(spec: PracticeNoteTypeSpec) -> list[str]:
    keys = [s.key for s in spec.sections] + [i.key for i in spec.inputs]
    return keys + [f.key for s in spec.sections for f in s.fields]


# ---------------------------------------------------------------------------
# The copy guard flags what is specific to a sample, and only that
# ---------------------------------------------------------------------------


def test_ordinary_headings_and_clinical_wording_are_never_copies() -> None:
    spec = PracticeNoteTypeSpec.model_validate(PROPOSAL)

    assert copied_paths(spec, SampleText([SAMPLE])) == []


def test_sample_specific_text_is_still_caught() -> None:
    index = SampleText([SAMPLE])

    assert index.copied("Note how Harbinger describes the mornings.")  # a name
    assert index.copied("e.g. walking through wet cement")  # quoted speech
    assert index.copied("Like improved sleep since the dose change and fewer evening worries")
    assert not index.copied("Changes in sleep and mood since the last visit.")


def test_no_part_is_ever_given_a_positional_name() -> None:
    copied = {
        **PROPOSAL,
        "sections": [
            {
                "key": "quill_harbinger",
                "label": "Quill Harbinger",
                "fields": [_field("harbinger_mornings", "Harbinger mornings", "Quill's mornings.")],
            },
            *PROPOSAL["sections"][1:5],
        ],
    }
    service, _ = _service(copied, RuntimeError("rewrite unavailable"))

    derived = service.derive([SAMPLE])

    assert not [k for k in _keys(derived.spec) if POSITIONAL.match(k)]
    assert copied_paths(derived.spec, SampleText([SAMPLE])) == []
    assert derived.spec.sections[1].label == "Mornings"
    assert derived.spec.sections[1].fields[0].label == "Mornings"


# ---------------------------------------------------------------------------
# Only the note is a note
# ---------------------------------------------------------------------------


def test_a_rationale_block_is_left_out_of_the_prompt_and_the_proposal() -> None:
    service, gateway = _service(PROPOSAL)

    derived = service.derive([SAMPLE])

    assert "Billing Justification" not in [s.label for s in derived.spec.sections]
    prompt = gateway.calls[0]["user_prompt"]
    assert "moderate complexity" not in prompt
    assert "Return in four weeks" in prompt
    (coverage,) = derived.coverage
    # The rationale block's lines, plus the client's name and date of birth.
    assert coverage.excluded == len(RATIONALE.strip().splitlines()) + 2
    assert not [p for p in coverage.unplaced if "complexity" in p]


def test_numbered_items_inside_a_rationale_block_stay_in_it() -> None:
    text = """\
Plan
Continue the current dose.
Electronically signed by,
Dr. Sample, MD
_____________________
Coding Rationale
1. Rationale for the visit code (low complexity)
1. Number and Complexity of Problems Addressed: Low
- Criterion: one stable chronic illness.
2. Risk of Complications: Moderate
Conclusion: Low problems and moderate risk support the code.
Data Reviewed: screening scales
Risk: Moderate: prescription drug management
"""

    stripped = strip_non_note(text)

    assert stripped.text.splitlines()[:2] == ["Plan", "Continue the current dose."]
    assert "Risk" not in stripped.text
    assert stripped.removed_lines == 8


def test_lines_that_introduce_content_are_structure_not_passages() -> None:
    text = (
        "Follow-up and next steps:\n"
        "Because the week was hard, I am making the following changes to the dose:\n"
        "Mental Status Exam (MSE)\n"
        "Return in two weeks for a medication check.\n"
    )

    found, _ = split_passages(text)

    assert [p.text for p in found] == ["Return in two weeks for a medication check."]


def test_a_marker_inside_a_paragraph_removes_only_that_paragraph() -> None:
    text = "Plan\nContinue.\n\nThis example is for educational purposes only.\nIgnore it.\n"

    stripped = strip_non_note(text)

    assert stripped.text.splitlines() == ["Plan", "Continue."]
    assert stripped.removed_lines == 2


# ---------------------------------------------------------------------------
# Visit facts get a home
# ---------------------------------------------------------------------------


def test_header_facts_become_an_encounter_section_and_inputs() -> None:
    service, _ = _service(PROPOSAL)

    spec = service.derive([SAMPLE]).spec

    encounter = spec.sections[0]
    assert encounter.label == "Encounter"
    assert [f.key for f in encounter.fields] == [
        "date_of_service",
        "visit_times",
        "visit_codes",
        "place_of_service",
        "client_location",
        "provider_location",
        "telehealth_attestation",
    ]
    assert [i.key for i in spec.inputs] == [
        "place_of_service",
        "client_location",
        "provider_location",
    ]


def test_facts_the_proposal_already_holds_are_not_added_twice() -> None:
    proposal = {
        **PROPOSAL,
        "sections": [
            {
                "key": "visit",
                "label": "Visit details",
                "fields": [
                    _field("dos", "Date of service", "The visit date."),
                    _field("pos", "Place of service", "Where it took place."),
                ],
            },
            *PROPOSAL["sections"][:5],
        ],
        "inputs": [{"key": "pos", "label": "Place of service"}],
    }
    service, _ = _service(proposal)

    spec = service.derive([SAMPLE]).spec

    visit = spec.sections[0]
    assert [f.key for f in visit.fields][:2] == ["dos", "pos"]
    assert "date_of_service" not in [f.key for f in visit.fields]
    assert "telehealth_attestation" in [f.key for f in visit.fields]
    assert [i.key for i in spec.inputs] == ["pos", "client_location", "provider_location"]


def test_a_note_without_a_header_gets_no_encounter_section() -> None:
    plain = "Plan\nContinue the current approach and meet again next week.\n"
    service, _ = _service(PROPOSAL)

    spec = service.derive([plain]).spec

    assert "Encounter" not in [s.label for s in spec.sections]
    assert spec.inputs == []


# ---------------------------------------------------------------------------
# Coverage reads labelled facts one by one
# ---------------------------------------------------------------------------

HEADER_LINES = """\
Client: Quill Harbinger    Provider: Dr. Sample, MD
Visit Date: March 4, 2026 CPT Codes: 99214, 90836
Visit Time: 10:00 AM - 10:45 AM Diagnosis: F41.1
Treatment Approach: Brief problem-solving and paced breathing.

Dr. Sample, MD
Dr. Sample, MD 3/5/2026
"""


def test_each_labelled_fact_on_a_line_is_its_own_passage() -> None:
    found, excluded = split_passages(HEADER_LINES)

    assert [p.text for p in found] == [
        "Visit Date: March 4, 2026",
        "CPT Codes: 99214, 90836",
        "Visit Time: 10:00 AM - 10:45 AM",
        "Diagnosis: F41.1",
        "Treatment Approach: Brief problem-solving and paced breathing.",
    ]
    # Two identity facts on the first line, and two signature lines.
    assert excluded == 4


def test_a_multi_fact_line_is_placed_when_its_facts_land_in_fields() -> None:
    extracted = {
        "encounter": {
            "date_of_service": "March 4, 2026",
            "visit_codes": "99214, 90836",
            "visit_times": "10:00 AM - 10:45 AM",
            "diagnosis": "F41.1",
        },
        "psychotherapy": {"treatment_approach": ""},
    }

    unplaced = unplaced_passages(HEADER_LINES, extracted)

    assert unplaced == ["Treatment Approach: Brief problem-solving and paced breathing."]
