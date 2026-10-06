# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``diagnoses`` field kind: diagnoses kept as the clinician stated them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.models import Patient, Transcript
from app.notes import NoteFieldDef, NoteSectionDef, NoteTypeDefinition
from app.notes.diagnoses import DIAGNOSES_SCHEMA, coerce_diagnoses, diagnosis_text
from app.notes.practice_types import PracticeFieldSpec
from app.services.export_pdf import note_paragraphs
from app.services.note_generation_service import (
    RegistryNoteGenerationService,
    _build_registry_response_schema,
    _coerce_registry_response,
    _fields_block,
)
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway

NOW = datetime(2026, 10, 6, tzinfo=UTC)

DEFINITION = NoteTypeDefinition(
    key="custom.eval",
    label="Evaluation",
    description="",
    tier="core",
    context="session",
    system_prompt="Draft it.",
    sections=(
        NoteSectionDef(
            key="assessment",
            label="Assessment",
            fields=(
                NoteFieldDef(key="diagnoses", label="Diagnoses", kind="diagnoses"),
                NoteFieldDef(key="rationale", label="Rationale", kind="text"),
            ),
        ),
    ),
)


def test_a_practice_field_can_be_diagnoses() -> None:
    assert PracticeFieldSpec(key="dx", label="Diagnoses", kind="diagnoses").kind == "diagnoses"


def test_the_model_is_asked_for_label_code_and_status_items() -> None:
    schema = _build_registry_response_schema(DEFINITION)

    assert schema["properties"]["assessment"]["properties"]["diagnoses"] == DIAGNOSES_SCHEMA
    item = DIAGNOSES_SCHEMA["items"]
    assert set(item["properties"]) == {"label", "code", "status"}
    assert item["required"] == ["label"]


def test_the_prompt_says_diagnoses_are_only_as_stated() -> None:
    block = _fields_block(DEFINITION)

    line = next(line for line in block.splitlines() if "* diagnoses" in line)
    assert "only diagnoses the clinician stated" in line
    assert "code only as the clinician said it" in line


def test_a_reply_is_kept_as_stated_with_blanks_as_none() -> None:
    reply = {
        "assessment": {
            "diagnoses": [
                {"label": " Generalized anxiety disorder ", "code": "F41.1", "status": ""},
                {"label": "ADHD, inattentive", "code": "F90.0", "status": "rule out"},
                {"label": "Insomnia"},
                {"label": "", "code": "F99"},
                "Major depressive disorder F32.1",
                None,
            ],
            "rationale": "As discussed.",
        }
    }

    content = _coerce_registry_response(DEFINITION, reply)

    assert content["assessment"]["diagnoses"] == [
        {"label": "Generalized anxiety disorder", "code": "F41.1", "status": None},
        {"label": "ADHD, inattentive", "code": "F90.0", "status": "rule out"},
        {"label": "Insomnia", "code": None, "status": None},
        # A bare line is never split: guessing which part is the code would invent one.
        {"label": "Major depressive disorder F32.1", "code": None, "status": None},
    ]


@pytest.mark.parametrize("raw", [None, "F32.1", {"label": "x"}])
def test_anything_but_a_list_is_no_diagnoses(raw: Any) -> None:
    assert coerce_diagnoses(raw) == []


def test_a_diagnosis_reads_as_one_line() -> None:
    assert diagnosis_text({"label": "GAD", "code": "F41.1", "status": None}) == "GAD (F41.1)"
    assert (
        diagnosis_text({"label": "ADHD", "code": "F90.0", "status": "rule out"})
        == "ADHD (F90.0), rule out"
    )
    assert diagnosis_text({"label": "Insomnia"}) == "Insomnia"
    assert diagnosis_text("an older plain line") == "an older plain line"


def test_the_exported_note_prints_diagnoses_as_lines() -> None:
    content = {
        "assessment": {
            "diagnoses": [
                {"label": "GAD", "code": "F41.1", "status": None},
                {"label": "ADHD", "code": "F90.0", "status": "rule out"},
            ],
            "rationale": "",
        }
    }

    assert note_paragraphs(content, DEFINITION) == [
        ("Assessment - Diagnoses", "GAD (F41.1); ADHD (F90.0), rule out")
    ]


class _Gateway(StructuredLLMGateway):
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def complete_structured(self, **_: Any) -> StructuredCompletion:
        return StructuredCompletion(data=self.data)


def test_a_generated_note_carries_structured_diagnoses() -> None:
    gateway = _Gateway(
        {"assessment": {"diagnoses": [{"label": "GAD", "code": "F41.1"}], "rationale": "x"}}
    )
    patient = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)

    generated = RegistryNoteGenerationService(llm_gateway=gateway).generate_note(
        DEFINITION.key,
        Transcript(format="txt", content="[00:01] Therapist: Hello"),
        patient,
        NOW,
        definition=DEFINITION,
    )

    assert generated.content["assessment"]["diagnoses"] == [
        {"label": "GAD", "code": "F41.1", "status": None}
    ]
