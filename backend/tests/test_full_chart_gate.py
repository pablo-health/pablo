# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which note types get the full chart, and so propose updates to it: decided by the definition.

The prescriber notes registered as built-ins, every type a practice saves
and a type based on a built-in all get the allergies, the current
medications and the history, and propose chart updates. SOAP and the other
formats written in code get the problem list alone and propose nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from app.chart_proposals.step import ChartProposalStep, proposes_chart_updates
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.chart_context import ChartContext, ChartHistoryField, ChartMedication
from app.notes.practice_spec import PracticeNoteTypeSpec
from app.notes.practice_types import resolve
from app.repositories import InMemoryChartHistoryRepository, InMemoryChartProposalRepository
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

NOW = datetime(2026, 10, 8, tzinfo=UTC)
TRANSCRIPT = Transcript(
    format="txt",
    content="[00:01] Therapist: Anything new?\n[00:05] Client: Update on work_school: laid off.",
)
CHART = ChartContext(
    allergy_status="recorded",
    allergies=({"substance": "Penicillin", "reaction": "Hives"},),
    medications=(ChartMedication("Sertraline", "100 mg", "every morning"),),
    history=(ChartHistoryField("work_school", "Teacher, full time.", date(2026, 6, 1)),),
)
PROPOSAL = {
    "proposals": [
        {
            "field_key": "work_school",
            "proposed_text": "Teacher until October; laid off, no longer teaching.",
            "what_changed": "Laid off",
            "evidence_segment_ids": [1],
        }
    ]
}


@pytest.fixture
def registry() -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return registry


def _based_follow_up(registry: NoteTypeRegistry) -> Any:
    spec = PracticeNoteTypeSpec.model_validate(
        {"label": "My follow-up", "base": "psychiatric_follow_up", "patch": {}}
    )
    return resolve("custom.my_follow_up", 1, registry.get("psychiatric_follow_up"), spec)


def _prompts(definition: Any) -> list[str]:
    """Every prompt the draft sent: the note's own call, and the extraction for a
    type whose chart-fed fields are written from the chart."""
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data={}))
    service = RegistryNoteGenerationService(llm_gateway=gateway)
    patient = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
    service.generate_note(
        definition.key,
        TRANSCRIPT,
        patient,
        NOW,
        inputs={"place_of_service": "In office"},
        definition=definition,
        chart=CHART,
    )
    return [str(call["user_prompt"]) for call in gateway.calls]


class _Proposing(RegistryNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            return PROPOSAL

        return complete


@pytest.mark.parametrize("which", ["built-in", "based"])
def test_a_prescriber_note_gets_the_full_chart_and_proposes(
    registry: NoteTypeRegistry, which: str
) -> None:
    definition = (
        registry.get("psychiatric_follow_up") if which == "built-in" else _based_follow_up(registry)
    )

    prompts = "\n".join(_prompts(definition))
    step = ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())
    # The draft prints the chart's work_school with what the visit said about it.
    draft = {
        "social_history": {"work_school": 'Teacher, full time. (stated this visit: "laid off")'}
    }
    drafted = step.draft(_Proposing(), definition, CHART, TRANSCRIPT, draft)

    assert definition.full_chart
    # Written against the allergies, the medications and the history: the note's
    # own call sees the medication list, and the extraction asks what the visit
    # said about each field printed from the chart.
    assert "allergies (Allergies): Penicillin (Hives)" in prompts
    assert "Sertraline 100 mg, every morning" in prompts
    assert "work_school (Work or school): Teacher, full time." in prompts
    assert drafted is not None
    assert [p.field_key for p in drafted.proposals] == ["work_school"]


@pytest.mark.parametrize("key", ["soap", "narrative"])
def test_a_format_written_in_code_gets_the_problem_list_alone_and_proposes_nothing(
    registry: NoteTypeRegistry, key: str
) -> None:
    definition = registry.get(key)

    (prompt,) = _prompts(definition)[:1]
    step = ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())

    assert not definition.full_chart
    assert not proposes_chart_updates(definition)
    assert "Penicillin" not in prompt
    assert "Sertraline" not in prompt
    assert "Teacher, full time." not in prompt
    assert step.draft(_Proposing(), definition, CHART, TRANSCRIPT, {}) is None
