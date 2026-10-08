# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Built-in note types written as data: the spec files under ``templates/``.

Each file is a note type in the same shape a practice saves its own — a
spec — plus the slug a practice type started from it saves under, the
fields a type based on it cannot hide, and synthetic sample visits for
trying a draft. Registered at startup beside the built-ins written in code,
they are both note types in their own right and bases a practice can adjust.

The sample transcripts name a client in every line, which is why they are
data here rather than interface copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from .practice_spec import PracticeNoteTypeSpec
from .practice_types import to_definition

if TYPE_CHECKING:
    from .registry import NoteTypeDefinition

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class SampleVisit:
    """A synthetic visit transcript to try a draft on."""

    id: str
    label: str
    transcript: str


@dataclass(frozen=True)
class SpecTemplate:
    key: str
    slug: str
    """The slug a practice type started from this one saves under, when it is free."""
    spec: PracticeNoteTypeSpec
    required_fields: tuple[str, ...]
    samples: tuple[SampleVisit, ...]

    def definition(self) -> NoteTypeDefinition:
        return to_definition(self.key, None, self.spec, required_fields=self.required_fields)


@cache
def spec_templates() -> tuple[SpecTemplate, ...]:
    """Every spec file, sorted by key."""
    return tuple(_load(path) for path in sorted(TEMPLATES_DIR.glob("*.json")))


def spec_template(key: str) -> SpecTemplate | None:
    return next((t for t in spec_templates() if t.key == key), None)


def _load(path: Path) -> SpecTemplate:
    data = json.loads(path.read_text())
    return SpecTemplate(
        key=data["id"],
        slug=data["slug"],
        spec=PracticeNoteTypeSpec.model_validate(data["spec"]),
        required_fields=tuple(data.get("required_fields", ())),
        samples=tuple(SampleVisit(**sample) for sample in data["samples"]),
    )
