# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the chart tells a draft: listed diagnoses with codes, and allergies.

The prompt is the part of generation a unit test can hold steady: the chart
block has to be there, in the right place, for the right note types, and the
rules that keep a draft from inventing a diagnosis or overruling a recorded
allergy have to travel with it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import (
    STATED_THIS_VISIT,
    ChartContext,
    ChartMedication,
    ChartProblem,
    allergies_line,
    chart_context_for,
    render_chart_block,
)
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.problems.models import Problem
from app.repositories import InMemoryMedicationRepository, InMemoryPatientProblemRepository
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.session_service import SessionService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

if TYPE_CHECKING:
    from app.notes.registry import NoteTypeDefinition

NOW = datetime(2026, 10, 6, tzinfo=UTC)
TRANSCRIPT = Transcript(format="txt", content="[00:01] Therapist: How has the week been?")


def _patient(allergy_status: str = "not_recorded") -> Patient:
    return Patient(
        id="p1",
        first_name="Sam",
        last_name="Sample",
        created_at=NOW,
        updated_at=NOW,
        allergy_status=allergy_status,
    )


def _problem(label: str, code: str | None, status: str, position: int) -> Problem:
    return Problem(
        id=f"pr{position}",
        patient_id="p1",
        label=label,
        icd10_code=code,
        status=status,
        position=position,
        added_at=NOW,
        updated_at=NOW,
    )


CHART = ChartContext(
    problems=(
        ChartProblem("Generalized anxiety disorder", "F41.1", "active"),
        ChartProblem("Insomnia", None, "active"),
        ChartProblem("Bipolar II disorder", None, "rule_out"),
    ),
    allergy_status="recorded",
    allergies=({"substance": "Penicillin", "reaction": "Hives", "severity": "moderate"},),
)


def test_chart_carries_active_and_rule_out_problems_but_not_resolved() -> None:
    chart = chart_context_for(
        _patient(allergy_status="nkda"),
        [
            _problem("GAD", "F41.1", "active", 0),
            _problem("Bipolar II", None, "rule_out", 1),
            _problem("PTSD", "F43.10", "resolved", 2),
        ],
    )
    assert [(p.label, p.status) for p in chart.problems] == [
        ("GAD", "active"),
        ("Bipolar II", "rule_out"),
    ]
    assert chart.allergy_status == "nkda"


def test_block_names_codes_and_marks_rule_outs_and_uncoded() -> None:
    block = render_chart_block(CHART, include_prescribing=False)
    assert "  - F41.1 Generalized anxiety disorder" in block
    assert "  - Insomnia (no code recorded)" in block
    assert "  - Bipolar II disorder — rule-out, not a diagnosis" in block
    assert "Never add a diagnosis that is neither on the problem list nor stated" in block
    assert STATED_THIS_VISIT in block
    assert "Allergies" not in block


def test_an_empty_list_reads_none_recorded() -> None:
    block = render_chart_block(ChartContext(), include_prescribing=True)
    assert "- Problem list: none recorded" in block
    assert "- Allergies: Not recorded" in block


@pytest.mark.parametrize(
    ("chart", "line"),
    [
        (ChartContext(allergy_status="nkda"), "No known drug allergies (NKDA)"),
        (ChartContext(), "Not recorded"),
        (CHART, "Penicillin (Hives, moderate)"),
        (
            ChartContext(allergy_status="recorded", allergies=({"substance": "Latex"},)),
            "Latex",
        ),
    ],
)
def test_allergies_line_for_each_state(chart: ChartContext, line: str) -> None:
    assert allergies_line(chart) == line


def test_the_chart_value_wins_over_the_transcript() -> None:
    block = render_chart_block(CHART, include_prescribing=True)
    assert "write the chart's value even if the transcript differs" in block


# --- Where the block lands, per note type --------------------------------------


@pytest.fixture
def registry() -> NoteTypeRegistry:
    reg = NoteTypeRegistry()
    register_builtin_note_types(reg)
    return reg


def _generate(
    registry: NoteTypeRegistry,
    note_type: str,
    chart: ChartContext | None,
    definition: NoteTypeDefinition | None = None,
) -> str:
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data={}))
    service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)
    service.generate_note(
        note_type, TRANSCRIPT, _patient(), NOW, definition=definition, chart=chart
    )
    return str(gateway.calls[0]["user_prompt"])


def test_soap_is_written_against_the_problem_list_without_allergies(
    registry: NoteTypeRegistry,
) -> None:
    prompt = _generate(registry, "soap", CHART)
    assert prompt.index("F41.1 Generalized anxiety disorder") < prompt.index("# Transcript")
    assert "Penicillin" not in prompt


def test_a_builtin_registry_type_is_written_against_the_problem_list(
    registry: NoteTypeRegistry,
) -> None:
    prompt = _generate(registry, "narrative", CHART)
    assert "F41.1 Generalized anxiety disorder" in prompt
    assert "Penicillin" not in prompt


def test_meeting_minutes_never_see_a_chart(registry: NoteTypeRegistry) -> None:
    prompt = _generate(registry, "meeting_summary", CHART)
    assert "Problem list" not in prompt
    assert "F41.1" not in prompt


def test_without_a_chart_there_is_no_chart_block(registry: NoteTypeRegistry) -> None:
    assert "Problem list" not in _generate(registry, "soap", None)


def _practice_definition(user_template: str | None) -> NoteTypeDefinition:
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Follow-up",
            "user_template": user_template,
            "sections": [{"key": "plan", "label": "Plan", "fields": [{"key": "a", "label": "A"}]}],
        }
    )
    return to_definition("custom.follow_up", 1, spec)


@pytest.mark.parametrize(
    "template",
    [
        None,
        "Chart:\n{chart}\n\nFields:\n{fields}\n\nTranscript:\n{transcript}",
        # Written before there was a chart to place: it goes first.
        "Fields:\n{fields}\n\nTranscript:\n{transcript}",
    ],
)
def test_a_practice_type_gets_problems_and_allergies(
    registry: NoteTypeRegistry, template: str | None
) -> None:
    definition = _practice_definition(template)
    prompt = _generate(registry, "custom.follow_up", CHART, definition=definition)
    assert "F41.1 Generalized anxiety disorder" in prompt
    assert "- Allergies: Penicillin (Hives, moderate)" in prompt
    assert prompt.index("Penicillin") < prompt.index("How has the week been?")
    assert "{chart}" not in prompt


# --- The medication list ---------------------------------------------------------


def _medication(
    name: str,
    dose: str,
    *,
    status: str = "active",
    frequency: str | None = None,
    category: str | None = None,
) -> dict[str, object]:
    return {
        "drug_name": name,
        "dose": dose,
        "status": status,
        "frequency": frequency,
        "category": category,
    }


def test_the_chart_carries_only_active_medications() -> None:
    chart = chart_context_for(
        _patient(),
        [],
        [
            _medication("Sertraline", "100 mg", frequency="every morning", category="psychiatric"),
            _medication("Hydroxyzine", "25 mg", status="discontinued"),
            _medication("Lithium", "300 mg", status="on_hold"),
        ],
    )
    assert chart.medications == (
        ChartMedication("Sertraline", "100 mg", "every morning", "psychiatric"),
    )


def test_each_medication_is_listed_with_dose_and_frequency() -> None:
    chart = ChartContext(
        medications=(
            ChartMedication("Sertraline", "100 mg", "every morning"),
            ChartMedication("Bupropion XL", "150 mg"),
        )
    )
    block = render_chart_block(chart, include_prescribing=True)
    assert (
        "- Current medications:\n  - Sertraline 100 mg, every morning\n  - Bupropion XL 150 mg\n"
        in (block)
    )
    assert "Psychiatric:" not in block


def test_psychiatric_and_other_medications_are_listed_apart() -> None:
    chart = ChartContext(
        medications=(
            ChartMedication("Lisinopril", "10 mg", "daily", "other"),
            ChartMedication("Sertraline", "50 mg AM / 25 mg PM", None, "psychiatric"),
            ChartMedication("Melatonin", "3 mg", "at bedtime"),
        )
    )
    block = render_chart_block(chart, include_prescribing=True)
    assert (
        "- Current medications:\n"
        "  - Psychiatric:\n"
        "    - Sertraline 50 mg AM / 25 mg PM\n"
        "  - Other:\n"
        "    - Lisinopril 10 mg, daily\n"
        "  - Not categorized:\n"
        "    - Melatonin 3 mg, at bedtime\n"
    ) in block


def test_no_medications_read_none_recorded() -> None:
    block = render_chart_block(ChartContext(), include_prescribing=True)
    assert "- Current medications: none recorded" in block


def test_medications_carry_the_as_written_rule_and_the_plan_line() -> None:
    block = render_chart_block(ChartContext(), include_prescribing=True)
    assert block.startswith("Chart (entered by the clinician; use these values as written):")
    assert (
        "- The medications field states the chart's list as given. A medication the "
        "clinician starts, stops or changes in this visit is written in the plan, not in "
        "the current list."
    ) in block


def test_a_note_with_no_place_for_medications_is_not_handed_them() -> None:
    chart = ChartContext(medications=(ChartMedication("Sertraline", "100 mg"),))
    block = render_chart_block(chart, include_prescribing=False)
    assert "Sertraline" not in block
    assert "medications" not in block


def test_soap_never_sees_the_medication_list(registry: NoteTypeRegistry) -> None:
    chart = ChartContext(medications=(ChartMedication("Sertraline", "100 mg"),))
    assert "Sertraline" not in _generate(registry, "soap", chart)


def test_a_practice_type_is_written_against_the_medication_list(
    registry: NoteTypeRegistry,
) -> None:
    chart = ChartContext(medications=(ChartMedication("Sertraline", "100 mg", "every morning"),))
    prompt = _generate(registry, "custom.follow_up", chart, definition=_practice_definition(None))
    assert "  - Sertraline 100 mg, every morning" in prompt
    assert prompt.index("Sertraline") < prompt.index("How has the week been?")


def test_a_session_draft_reads_the_medication_list() -> None:
    """The session worker's chart carries the medications its clinician can see."""
    patient = _patient()
    medications = InMemoryMedicationRepository()
    medications.grant_access(patient.id, "u1")
    medications.create(
        {"id": "m1", "patient_id": patient.id, **_medication("Sertraline", "100 mg")}, "u1"
    )
    service = SessionService(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        InMemoryPatientProblemRepository(),
        medications,
    )

    chart = service._chart_for(patient, "u1")
    outsider = service._chart_for(patient, "someone-else")

    assert chart is not None
    assert chart.medications == (ChartMedication("Sertraline", "100 mg"),)
    assert outsider is not None
    assert outsider.medications == ()
