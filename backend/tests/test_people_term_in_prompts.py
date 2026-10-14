# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's word for the people they see, in the prompts a note is drafted with.

A clinician set to "patients" is drafted for as "the patient", one set to
"clients" (or to nothing, the default) as "the client": in the built-in
prescriber types' system prompts, which place it as ``{term}``, and in the
chart-proposal prompt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from app.chart_proposals.drafting import build_prompt
from app.chart_proposals.step import ChartProposalStep
from app.models import Patient, Transcript
from app.notes.chart_context import ChartContext
from app.notes.practice_types import PracticeNoteTypeSpec, practice_key, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.people_term_lookup import PeopleTermLookup
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryClinicianProfileRepository,
    InMemoryPatientProblemRepository,
    InMemoryUserRepository,
)
from app.services.chart_field_extraction import SCHEMA_TITLE
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.session_service import SessionService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

USER = "user123"
NOW = datetime(2026, 10, 8, tzinfo=UTC)
PATIENT = Patient(id="p-1", first_name="Sam", last_name="Sample", created_at=NOW, updated_at=NOW)


def _lookup(term: str | None) -> PeopleTermLookup:
    users = InMemoryUserRepository()
    prefs = users.get_preferences(USER)
    prefs.people_term = term  # type: ignore[assignment]
    users.save_preferences(USER, prefs)
    return PeopleTermLookup(users, InMemoryClinicianProfileRepository())


@pytest.fixture(autouse=True)
def _no_practice() -> object:
    with patch("app.auth.service._resolve_practice_from_email", return_value=None):
        yield


@pytest.mark.parametrize(
    ("term", "person"), [("patients", "patient"), ("clients", "client"), (None, "client")]
)
def test_the_lookup_gives_the_clinicians_word(term: str | None, person: str) -> None:
    assert _lookup(term).person(USER) == person


def test_an_unknown_clinician_gets_the_default() -> None:
    assert _lookup("patients").person("nobody") == "client"


def test_the_chart_a_step_builds_names_the_person_in_the_readers_word() -> None:
    step = ChartProposalStep(
        InMemoryChartProposalRepository(),
        InMemoryChartHistoryRepository(),
        people=_lookup("patients"),
    )

    assert step.chart(PATIENT, USER).person == "patient"
    assert (
        ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())
        .chart(PATIENT, USER)
        .person
        == "client"
    )


def test_a_session_draft_reads_the_chart_in_the_clinicians_word() -> None:
    service = SessionService(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        problem_repo=InMemoryPatientProblemRepository(),
        people=_lookup("patients"),
    )

    chart = service._chart_for(PATIENT, USER)

    assert chart is not None
    assert chart.person == "patient"


def _system_prompt(template: str, person: str) -> str:
    spec = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    definition = to_definition(practice_key(template), 1, PracticeNoteTypeSpec.model_validate(spec))
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data={}))
    RegistryNoteGenerationService(llm_gateway=gateway).generate_note(
        definition.key,
        Transcript(format="txt", content="[00:01] Therapist: How have you been?"),
        PATIENT,
        NOW,
        definition=definition,
        chart=ChartContext(person=person),
    )
    # The note's own call; the extraction beside it has a system prompt of its own.
    draft = next(c for c in gateway.calls if c["response_schema"].get("title") != SCHEMA_TITLE)
    return str(draft["system_prompt"])


@pytest.mark.parametrize("template", ["psychiatric_follow_up", "psychiatric_evaluation"])
@pytest.mark.parametrize(("person", "other"), [("patient", "client"), ("client", "patient")])
def test_a_drafts_system_prompt_names_the_person_in_the_clinicians_word(
    template: str, person: str, other: str
) -> None:
    prompt = _system_prompt(template, person)

    assert f'Refer to the person seen as "the {person}" or with they/them' in prompt
    assert f'referred to as "the {person}"' in prompt
    assert f"the {other}" not in prompt
    assert "{term}" not in prompt
    assert "never he, she, his or her" in prompt


@pytest.mark.parametrize("person", ["patient", "client"])
def test_the_proposal_prompt_names_the_person_in_the_clinicians_word(person: str) -> None:
    prompt = build_prompt(ChartContext(person=person), "[S0] hello")

    assert f'Refer to the person seen as "the {person}"' in prompt
