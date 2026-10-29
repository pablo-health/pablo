# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The stored shape of a practice-defined note type.

A spec is either **full** — its own sections, inputs and prompts — or
**based** on another spec-shaped type: it names a ``base`` and carries a
``patch`` (parts added, hidden or relabelled; instructions appended), and
takes everything else from the base when it is read. See
:mod:`app.notes.note_type_patch` for how a patch is checked and applied.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .field_sources import FIELD_SOURCES, kind_for_source

_PART_KEY = r"^[a-z][a-z0-9_]{0,39}$"

_MIN_CHOICE_OPTIONS = 2

_INPUT_PLACEHOLDER = re.compile(r"\{inputs\.([a-z][a-z0-9_]*)\}")


def _omit_when_none(value: object) -> bool:
    return value is None


def _omit_when_false(value: object) -> bool:
    return value is False


class PracticeFieldSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "list", "diagnoses"] = "text"
    ai_hint: str = Field(default="", max_length=2000)
    source: str | None = Field(default=None, max_length=40, exclude_if=_omit_when_none)
    """What code prints this field from, when it is not drafted by the model
    (see :mod:`app.notes.field_sources`)."""

    @model_validator(mode="after")
    def _known_source(self) -> Self:
        if self.source is None:
            return self
        if self.source not in FIELD_SOURCES:
            raise ValueError(f"field {self.key!r} names an unknown source {self.source!r}")
        if self.kind != kind_for_source(self.source):
            raise ValueError(
                f"field {self.key!r} prints {self.source!r}, which takes the "
                f"{kind_for_source(self.source)!r} kind"
            )
        return self


class PracticeSectionSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    fields: list[PracticeFieldSpec] = Field(min_length=1, max_length=40)
    review_only: bool = Field(default=False, exclude_if=_omit_when_false)
    """Drafted for the clinician to review beside the note, never part of the note itself."""

    @model_validator(mode="after")
    def _unique_field_keys(self) -> Self:
        require_unique([f.key for f in self.fields], f"field keys in section {self.key!r}")
        return self


class PracticeInputSpec(BaseModel):
    key: str = Field(pattern=_PART_KEY)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "choice"] = "text"
    options: list[str] = Field(default_factory=list, max_length=20)
    required: bool = False
    default: str | None = Field(default=None, max_length=200, exclude_if=_omit_when_none)
    """The value a note of this type takes when the clinician has not chosen one."""

    @model_validator(mode="after")
    def _options_match_kind(self) -> Self:
        if self.kind == "choice" and len(self.options) < _MIN_CHOICE_OPTIONS:
            raise ValueError(f"input {self.key!r} is a choice and needs at least two options")
        if self.kind == "text" and self.options:
            raise ValueError(f"input {self.key!r} is free text and takes no options")
        require_unique(self.options, f"options of input {self.key!r}")
        if self.kind == "choice" and self.default is not None and self.default not in self.options:
            raise ValueError(f"the default of input {self.key!r} is not one of its options")
        return self


class AddedSection(BaseModel):
    """A section the practice adds, placed after ``after`` (a base section) or at the end."""

    model_config = ConfigDict(extra="forbid")

    section: PracticeSectionSpec
    after: str | None = None


class AddedField(BaseModel):
    """A field the practice adds to a base section, after ``after`` or at the section's end."""

    model_config = ConfigDict(extra="forbid")

    section: str
    field: PracticeFieldSpec
    after: str | None = None


class PartOverride(BaseModel):
    """A new label or hint for a base section (``path`` is its key) or field (``section.field``).

    There is deliberately no ``kind``: a patch cannot change what shape a
    field's content takes.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=81)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    ai_hint: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.label is None and self.ai_hint is None:
            raise ValueError(f"override of {self.path!r} changes neither the label nor the hint")
        return self


class NoteTypePatch(BaseModel):
    """What a based type changes about its base. Every path names a part of the base."""

    model_config = ConfigDict(extra="forbid")

    add_sections: list[AddedSection] = Field(default_factory=list, max_length=20)
    add_fields: list[AddedField] = Field(default_factory=list, max_length=100)
    hide_fields: list[str] = Field(default_factory=list, max_length=200)
    hide_sections: list[str] = Field(default_factory=list, max_length=20)
    override: list[PartOverride] = Field(default_factory=list, max_length=200)
    add_inputs: list[PracticeInputSpec] = Field(default_factory=list, max_length=10)
    system_prompt_append: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def _no_repeats(self) -> Self:
        require_unique([a.section.key for a in self.add_sections], "added section keys")
        require_unique([f"{a.section}.{a.field.key}" for a in self.add_fields], "added fields")
        require_unique([i.key for i in self.add_inputs], "added input keys")
        require_unique(self.hide_fields, "hidden fields")
        require_unique(self.hide_sections, "hidden sections")
        require_unique([o.path for o in self.override], "overridden paths")
        return self

    def additions(self) -> int:
        return len(self.add_sections) + len(self.add_fields) + len(self.add_inputs)

    def hidden(self) -> int:
        return len(self.hide_sections) + len(self.hide_fields)


class PracticeNoteTypeSpec(BaseModel):
    """The stored, practice-authored body of a note type.

    ``base`` and ``patch`` are left out of the serialized form when unset,
    so a full spec stores exactly as it always has.
    """

    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    system_prompt: str = Field(default="", max_length=20_000)
    user_template: str | None = Field(default=None, max_length=20_000)
    # Room for a base's own sections and inputs and as many as a patch may add.
    sections: list[PracticeSectionSpec] = Field(default_factory=list, max_length=40)
    inputs: list[PracticeInputSpec] = Field(default_factory=list, max_length=20)
    base: str | None = Field(default=None, max_length=30, exclude_if=_omit_when_none)
    patch: NoteTypePatch | None = Field(default=None, exclude_if=_omit_when_none)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.base is None) != (self.patch is None):
            raise ValueError("a base and a patch go together; send both or neither")
        if self.base is not None:
            _require_nothing_of_its_own(self)
            return self
        if not self.sections:
            raise ValueError("a note type needs at least one section")
        require_unique([s.key for s in self.sections], "section keys")
        require_unique([i.key for i in self.inputs], "input keys")
        if self.user_template is not None:
            if "{transcript}" not in self.user_template:
                raise ValueError("user_template must include {transcript}")
            declared = {i.key for i in self.inputs}
            for name in _INPUT_PLACEHOLDER.findall(self.user_template):
                if name not in declared:
                    raise ValueError(f"user_template references undeclared {{inputs.{name}}}")
        return self


def _require_nothing_of_its_own(spec: PracticeNoteTypeSpec) -> None:
    """A based type takes its structure and prompts from the base; the patch adjusts them."""
    if spec.sections:
        raise ValueError("a type with a base carries no sections of its own; add them in the patch")
    if spec.inputs:
        raise ValueError("a type with a base carries no inputs of its own; add them in the patch")
    if spec.system_prompt.strip() or spec.user_template is not None:
        raise ValueError(
            "a type with a base uses the base's prompts; add instructions in the patch"
        )


def require_unique(values: list[str], what: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {what}")
