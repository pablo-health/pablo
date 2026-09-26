# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note types a practice defines for itself.

A practice-defined type is data, not code: sections and fields with their
generation hints, a system prompt, an optional user-prompt template, and
the inputs a clinician supplies when a note is generated. It is stored in
the practice's own schema, so one practice never sees another's types, and
resolved alongside the built-in registry.

Two rules keep a stored definition from weakening generation:

- **The generation floor is appended after the practice's system prompt.**
  Whatever the practice writes, the model still drafts only from the
  transcript, does not diagnose or advise, and records rather than answers
  any statement of risk.
- **Versions are immutable.** Saving a type creates a new version; each note
  records the version it was generated with, so an older note keeps
  rendering against the fields it was written for.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, Field, model_validator

from .registry import (
    PRACTICE_KEY_PREFIX,
    NoteFieldDef,
    NoteInputDef,
    NoteSectionDef,
    NoteTypeDefinition,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from ..models import Transcript
    from ..repositories.practice_note_type import PracticeNoteTypeRepository, StoredNoteType

SLUG_PATTERN = r"^[a-z][a-z0-9_]{0,22}$"
"""A practice key is ``custom.<slug>``; the note_type column holds 30 characters."""

_PART_KEY = r"^[a-z][a-z0-9_]{0,39}$"

_MIN_CHOICE_OPTIONS = 2

GENERATION_FLOOR = (
    "The instructions above were written by the practice. These rules take "
    "precedence over them:\n"
    "- Draft only from the supplied transcript and inputs. Never invent "
    "statements, events, dates, or commitments. When the transcript does "
    "not support a field, leave it empty or say plainly that it was not "
    "covered.\n"
    "- Do not diagnose, recommend treatment or medication changes, or give "
    "medical advice.\n"
    "- If the transcript contains any statement about risk of harm to self "
    "or others, record what was said, quoted, in the most relevant field. "
    "Do not assess its severity and do not draft a response to it.\n"
    "- Respond only with the requested structured fields."
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a documentation assistant. Populate the requested note "
    "structure from the supplied transcript."
)

_PLACEHOLDER = re.compile(r"\{(transcript|session_date|fields|inputs\.[a-z][a-z0-9_]*)\}")


class PracticeFieldSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "list"] = "text"
    ai_hint: str = Field(default="", max_length=2000)


class PracticeSectionSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    fields: list[PracticeFieldSpec] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _unique_field_keys(self) -> Self:
        _require_unique([f.key for f in self.fields], f"field keys in section {self.key!r}")
        return self


class PracticeInputSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "choice"] = "text"
    options: list[str] = Field(default_factory=list, max_length=20)
    required: bool = False

    @model_validator(mode="after")
    def _options_match_kind(self) -> Self:
        if self.kind == "choice" and len(self.options) < _MIN_CHOICE_OPTIONS:
            raise ValueError(f"input {self.key!r} is a choice and needs at least two options")
        if self.kind == "text" and self.options:
            raise ValueError(f"input {self.key!r} is free text and takes no options")
        _require_unique(self.options, f"options of input {self.key!r}")
        return self


class PracticeNoteTypeSpec(BaseModel):
    """The stored, practice-authored body of a note type."""

    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    system_prompt: str = Field(default="", max_length=20_000)
    user_template: str | None = Field(default=None, max_length=20_000)
    sections: list[PracticeSectionSpec] = Field(min_length=1, max_length=20)
    inputs: list[PracticeInputSpec] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require_unique([s.key for s in self.sections], "section keys")
        _require_unique([i.key for i in self.inputs], "input keys")
        if self.user_template is not None:
            if "{transcript}" not in self.user_template:
                raise ValueError("user_template must include {transcript}")
            declared = {i.key for i in self.inputs}
            for name in _PLACEHOLDER.findall(self.user_template):
                if name.startswith("inputs.") and name.removeprefix("inputs.") not in declared:
                    raise ValueError(f"user_template references undeclared {{{name}}}")
        return self


def _require_unique(values: list[str], what: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {what}")


def with_generation_floor(system_prompt: str) -> str:
    """The practice's system prompt (or a neutral default) with the floor after it."""
    return f"{system_prompt.strip() or _DEFAULT_SYSTEM_PROMPT}\n\n{GENERATION_FLOOR}"


def practice_key(slug: str) -> str:
    return f"{PRACTICE_KEY_PREFIX}{slug}"


def to_definition(key: str, version: int, spec: PracticeNoteTypeSpec) -> NoteTypeDefinition:
    """Build the registry definition a stored spec generates with."""
    return NoteTypeDefinition(
        key=key,
        label=spec.label,
        description=spec.description,
        tier="core",
        context="session",
        system_prompt=with_generation_floor(spec.system_prompt),
        user_template=spec.user_template,
        version=version,
        inputs=tuple(
            NoteInputDef(
                key=i.key,
                label=i.label,
                kind=i.kind,
                options=tuple(i.options),
                required=i.required,
            )
            for i in spec.inputs
        ),
        sections=tuple(
            NoteSectionDef(
                key=s.key,
                label=s.label,
                fields=tuple(
                    NoteFieldDef(key=f.key, label=f.label, kind=f.kind, ai_hint=f.ai_hint)
                    for f in s.fields
                ),
            )
            for s in spec.sections
        ),
    )


def validate_note_inputs(
    definition: NoteTypeDefinition, inputs: Mapping[str, str] | None
) -> dict[str, str]:
    """Check supplied inputs against what the type declares; return the kept values.

    Blank values are dropped. Raises :class:`ValueError` naming the first
    problem: an undeclared key, a choice outside its options, or a missing
    required input.
    """
    declared = {i.key: i for i in definition.inputs}
    kept: dict[str, str] = {}
    for key, raw in (inputs or {}).items():
        if key not in declared:
            raise ValueError(f"{definition.key!r} has no input {key!r}")
        value = raw.strip()
        if not value:
            continue
        spec = declared[key]
        if spec.kind == "choice" and value not in spec.options:
            raise ValueError(f"{value!r} is not an option for {key!r}")
        kept[key] = value
    missing = [i.key for i in definition.inputs if i.required and i.key not in kept]
    if missing:
        raise ValueError(f"missing required input {missing[0]!r}")
    return kept


def render_user_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    session_date: datetime,
    inputs: Mapping[str, str],
    fields_block: str,
) -> str:
    """Render the type's template, or a default layout when it has none.

    Only the known placeholders are substituted, so any other braces a
    practice writes (a JSON example, say) pass through untouched.
    """
    date = session_date.isoformat().split("T", 1)[0]
    if definition.user_template is None:
        lines = [f"Produce a {definition.label} note.", "", fields_block, ""]
        lines.extend(_inputs_lines(definition, inputs))
        lines.extend([f"Session date: {date}", "", "Transcript:", transcript.content])
        return "\n".join(lines)

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "transcript":
            return transcript.content
        if name == "session_date":
            return date
        if name == "fields":
            return fields_block
        return inputs.get(name.removeprefix("inputs."), "not provided")

    return _PLACEHOLDER.sub(substitute, definition.user_template)


def _inputs_lines(definition: NoteTypeDefinition, inputs: Mapping[str, str]) -> list[str]:
    if not definition.inputs:
        return []
    lines = ["Inputs:"]
    lines.extend(f"- {i.label}: {inputs.get(i.key, 'not provided')}" for i in definition.inputs)
    lines.append("")
    return lines


class RepositoryPracticeNoteTypeSource:
    """Resolve practice note types from the caller's tenant schema.

    Takes a repository factory rather than a repository: the source is set
    once at startup, and each lookup must use the request's own session.
    """

    def __init__(self, repository: Callable[[], PracticeNoteTypeRepository]) -> None:
        self._repository = repository

    def get(self, key: str, version: int | None = None) -> NoteTypeDefinition | None:
        stored = self._repository().get(key, version)
        return stored_to_definition(stored) if stored else None

    def is_active(self, key: str) -> bool:
        stored = self._repository().get(key)
        return stored is not None and stored.retired_at is None

    def all_active(self) -> list[NoteTypeDefinition]:
        return [
            stored_to_definition(stored)
            for stored in self._repository().list_latest()
            if stored.retired_at is None
        ]


def stored_to_definition(stored: StoredNoteType) -> NoteTypeDefinition:
    spec = PracticeNoteTypeSpec.model_validate(stored.definition)
    return to_definition(stored.key, stored.version, spec)
