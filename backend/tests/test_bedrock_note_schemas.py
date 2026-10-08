# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every schema a long structured call sends survives the Bedrock leg.

Schemas are written in the dialect Gemini accepts; a Bedrock fallback sends
them as standard JSON Schema through :func:`to_json_schema`. Each schema a
note draft, an import or a derive can send is translated here, checked to be
a valid JSON Schema of the shape Bedrock's tool input takes, and checked to
accept an answer of the shape a model gives, which then parses into the note.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.diagnoses import DIAGNOSES_SCHEMA
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.notes.visit_times import PSYCHOTHERAPY_SECTION_KEY
from app.services.bedrock_structured_llm_gateway import to_json_schema
from app.services.note_generation_service import (
    _SOAP_ATTRIBUTION_SCHEMA,
    _build_registry_response_schema,
    _coerce_registry_response,
)
from app.services.note_import_service import _build_extract_schema
from app.services.note_type_derive_service import derive_response_schema
from app.services.psychotherapy_start import START_KEY, START_SCHEMA
from jsonschema import Draft202012Validator

from .test_generation_fallback import instance
from .test_note_type_derive import PROPOSAL

TEMPLATES = TEMPLATES_DIR


def _builtin() -> list[NoteTypeDefinition]:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return [d for d in registry.all() if not d.restricted]


def _templates() -> list[NoteTypeDefinition]:
    return [
        to_definition(
            f"custom.{path.stem}",
            1,
            PracticeNoteTypeSpec.model_validate(json.loads(path.read_text())["spec"]),
        )
        for path in sorted(TEMPLATES.glob("*.json"))
    ]


def _draft_schema(definition: NoteTypeDefinition) -> dict[str, Any]:
    """What a draft asks for, the psychotherapy start included where it is asked."""
    schema = _build_registry_response_schema(definition)
    if any(s.key == PSYCHOTHERAPY_SECTION_KEY for s in definition.sections):
        schema["properties"][START_KEY] = START_SCHEMA
    return schema


_DEFINITIONS = _builtin() + _templates()


def _keys(node: Any) -> set[str]:
    """Every key used anywhere in a schema."""
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _bedrock_ready(schema: dict[str, Any]) -> dict[str, Any]:
    translated = to_json_schema(schema)
    assert translated["type"] == "object", "a tool's input is an object"
    Draft202012Validator.check_schema(translated)
    assert not {"nullable", "$ref", "$defs"} & _keys(translated)
    return translated


def test_the_templates_and_builtins_are_all_here() -> None:
    keys = {d.key for d in _DEFINITIONS}
    assert {"soap", "custom.psychiatric_evaluation", "custom.psychiatric_follow_up"} <= keys


@pytest.mark.parametrize("definition", _DEFINITIONS, ids=[d.key for d in _DEFINITIONS])
def test_a_draft_schema_translates_and_its_answer_parses(definition: NoteTypeDefinition) -> None:
    translated = _bedrock_ready(_draft_schema(definition))

    answer = instance(translated)
    Draft202012Validator(translated).validate(answer)
    content = _coerce_registry_response(definition, answer)
    assert set(content) == {s.key for s in definition.sections}
    for section in definition.sections:
        for field in section.fields:
            assert content[section.key][field.key], f"{section.key}.{field.key} came back empty"


@pytest.mark.parametrize("definition", _DEFINITIONS, ids=[d.key for d in _DEFINITIONS])
def test_an_import_schema_translates(definition: NoteTypeDefinition) -> None:
    translated = _bedrock_ready(_build_extract_schema(definition))
    Draft202012Validator(translated).validate(instance(translated))


def test_the_large_templates_carry_diagnoses_and_the_psychotherapy_start() -> None:
    """The cases the plain registry types do not exercise."""
    schemas = [to_json_schema(_draft_schema(d)) for d in _templates()]
    kinds = [
        field
        for schema in schemas
        for section in schema["properties"].values()
        for field in section.get("properties", {}).values()
    ]
    assert to_json_schema(DIAGNOSES_SCHEMA) in kinds
    assert any(START_KEY in schema["properties"] for schema in schemas)


def test_a_diagnosis_needs_its_label_on_the_bedrock_leg() -> None:
    items = Draft202012Validator(to_json_schema(DIAGNOSES_SCHEMA))
    assert items.is_valid([{"label": "Generalized anxiety disorder", "code": "F41.1"}])
    assert not items.is_valid([{"code": "F41.1"}])
    assert to_json_schema(DIAGNOSES_SCHEMA)["items"]["title"] == "StatedDiagnosis"


def test_the_psychotherapy_start_translates() -> None:
    start = Draft202012Validator(to_json_schema(START_SCHEMA))
    assert start.is_valid(
        {"transcript_time": "00:02:10", "cued_by_clinician": True, "stated_clock_time": ""}
    )
    assert not start.is_valid({"cued_by_clinician": "yes"})


def test_the_derive_schema_translates_and_accepts_a_proposal() -> None:
    translated = _bedrock_ready(derive_response_schema())
    assert translated["title"] == "PracticeNoteTypeSpec"
    Draft202012Validator(translated).validate(PROPOSAL)


def test_the_attribution_schema_translates() -> None:
    translated = _bedrock_ready(_SOAP_ATTRIBUTION_SCHEMA)
    Draft202012Validator(translated).validate({"attributions": [{"claim": 0, "segments": [1]}]})
