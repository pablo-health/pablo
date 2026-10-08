# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""No model is ever asked for a billing code.

The E/M code follows the clinician's MDM choices and the add-on follows the
psychotherapy minutes, both through :mod:`app.notes.mdm`. A response schema,
or the hint a field carries to the model, that names a code would invite the
model to pick one. This walks what a draft and an import ask for, for every
built-in type and every starter template.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.services.note_generation_service import _build_registry_response_schema
from app.services.note_import_service import _build_extract_schema
from app.services.psychotherapy_start import START_SCHEMA

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "settings"
    / "noteTypes"
    / "templates"
)

# A CPT code (five digits starting 9, the E/M and psychiatry range) or a HCPCS G code.
BILLING_CODE = re.compile(r"\b(?:9\d{4}|G\d{4})\b")


def _definitions() -> list[NoteTypeDefinition]:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    templates = [
        to_definition(
            f"custom.{path.stem}",
            1,
            PracticeNoteTypeSpec.model_validate(json.loads(path.read_text())["spec"]),
        )
        for path in sorted(TEMPLATES.glob("*.json"))
    ]
    return registry.all() + templates


_DEFINITIONS = _definitions()


def _strings(node: Any) -> list[str]:
    """Every key and string value anywhere in a schema: names, descriptions, enums."""
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (k, *_strings(v))]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [node] if isinstance(node, str) else []


def test_the_pattern_catches_a_code() -> None:
    assert BILLING_CODE.search("Billing 99214 plus 90836")
    assert BILLING_CODE.search("G2211")
    assert not BILLING_CODE.search("F41.1")


@pytest.mark.parametrize("definition", _DEFINITIONS, ids=[d.key for d in _DEFINITIONS])
def test_no_schema_or_field_hint_names_a_code(definition: NoteTypeDefinition) -> None:
    asked = [
        *_strings(_build_registry_response_schema(definition)),
        *_strings(_build_extract_schema(definition)),
        *_strings(START_SCHEMA),
        *(f.ai_hint for s in definition.sections for f in s.fields),
    ]
    named = [text for text in asked if BILLING_CODE.search(text)]
    assert not named, f"{definition.key} asks the model about a code: {named}"
