# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from app.notes.chart_fields import without_rendered
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.section_calls import (
    BACKGROUND,
    CLINICAL_HISTORY,
    SECTION_CALLS,
    code_written_fields,
    section_call_fields,
    without_fields,
)
from app.notes.spec_templates import TEMPLATES_DIR
from app.services.history_section_call import (
    BACKGROUND_SCHEMA_TITLE,
    CLINICAL_HISTORY_SCHEMA_TITLE,
    compose,
    response_schema,
)

if TYPE_CHECKING:
    from app.notes.registry import NoteTypeDefinition


def _definition(template: str) -> NoteTypeDefinition:
    raw = json.loads((TEMPLATES_DIR / f"{template}.json").read_text())["spec"]
    spec = PracticeNoteTypeSpec.model_validate(raw)
    return to_definition(f"custom.{template}", 1, spec)


@pytest.mark.parametrize("template", ["psychiatric_evaluation", "psychiatric_follow_up"])
def test_related_history_sections_route_to_two_bounded_lanes(template: str) -> None:
    definition = _definition(template)
    clinical = section_call_fields(definition, CLINICAL_HISTORY)
    background = section_call_fields(definition, BACKGROUND)

    expected_clinical = {"substance_use", "psychiatric_history", "trauma_history"}
    expected_background = {"social_history", "medical_history", "family_history"}
    if template == "psychiatric_evaluation":
        expected_background.add("other_providers")
    else:
        expected_clinical.clear()
        expected_background.clear()
    assert {field.section for field in clinical} == expected_clinical
    assert {field.section for field in background} == expected_background
    assert len(clinical) <= 24
    assert len(background) <= 24


def test_lane_schema_requires_every_declared_section_and_field() -> None:
    fields = section_call_fields(_definition("psychiatric_evaluation"), CLINICAL_HISTORY)
    schema = response_schema(fields, title=CLINICAL_HISTORY_SCHEMA_TITLE)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for section in schema["properties"].values():
        assert section["additionalProperties"] is False
        assert set(section["required"]) == set(section["properties"])


def test_evaluation_main_schema_is_bounded_after_every_parallel_lane_is_removed() -> None:
    definition = without_rendered(_definition("psychiatric_evaluation"))
    routed = [
        field
        for call in set(SECTION_CALLS.values())
        for field in section_call_fields(definition, call)
    ]
    main = without_fields(definition, [*routed, *code_written_fields(definition)])

    assert sum(len(section.fields) for section in main.sections) <= 24
    assert {section.key for section in main.sections} >= {"assessment", "treatment_plan"}


def test_background_schema_has_its_own_title() -> None:
    fields = section_call_fields(_definition("psychiatric_evaluation"), BACKGROUND)
    assert response_schema(fields, title=BACKGROUND_SCHEMA_TITLE)["title"] == (
        BACKGROUND_SCHEMA_TITLE
    )


def test_uncovered_text_uses_each_field_hint_and_lists_are_coerced() -> None:
    fields = section_call_fields(_definition("psychiatric_evaluation"), CLINICAL_HISTORY)
    reply: dict[str, Any] = {
        "substance_use": {"alcohol": "Not stated."},
        "psychiatric_history": {"medication_trials": [" Sertraline ", ""]},
    }

    drafted = compose(reply, fields)

    assert drafted["substance_use"]["alcohol"] == "Not asked."
    assert drafted["psychiatric_history"]["medication_trials"] == ["Sertraline"]
