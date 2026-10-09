# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which sections of a note are drafted by a call of their own, apart from the main draft.

A section is routed by its key. The prescriber notes' risk assessment, mental
status exam and measures (``risk``, ``mse``, ``measures``, the same keys in the
follow-up and the evaluation) are drafted together by one small call
(:mod:`app.services.risk_section_call`), so the rules about whose words are
quoted live in that call alone. The history of present illness is drafted by
another (:mod:`app.services.hpi_section_call`): the follow-up's ``subjective``
section (chief complaint, the symptom domains, course, stressors, functioning,
adherence and side effects) and the evaluation's ``chief_complaint``, ``hpi``
and ``psychiatric_ros``. Every field of a routed section goes with it, a field
a practice added to a based type included; the main draft's schema and prompt
no longer carry them.

Only a type written as a spec is routed (the prescriber templates, a type based
on one, a practice's own): the formats written in code keep one call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from .registry import NoteFieldDef, NoteTypeDefinition

RISK_MSE = "risk_mse"
"""The call drafting risk, mental status and measures."""

HPI = "hpi"
"""The call drafting the history of present illness, domain by domain."""

SECTION_CALLS: Mapping[str, str] = {
    "risk": RISK_MSE,
    "mse": RISK_MSE,
    "measures": RISK_MSE,
    "subjective": HPI,
    "chief_complaint": HPI,
    "hpi": HPI,
    "psychiatric_ros": HPI,
}
"""Section key to the call that drafts it."""

QUOTING_SECTIONS = frozenset({"risk"})
"""Sections whose text fields carry the client's own words, each citing the lines it is from."""

QUOTING_FIELDS = frozenset({"chief_complaint"})
"""Fields, by key in any routed section, whose text carries the client's own words the same way."""

_ROUTED_KINDS = frozenset({"text", "list"})
"""Field kinds a section call drafts; any other kind stays with the main draft."""

MODEL_KEY_PREFIX = "note_generation"
"""A call's model key is this, a dot and the call's name: ``note_generation.risk_mse``
names its model in ``AI_MODELS``, and the note model drafts it when unnamed."""


def model_key(call: str) -> str:
    return f"{MODEL_KEY_PREFIX}.{call}"


@dataclass(frozen=True)
class SectionField:
    section: str
    field: NoteFieldDef
    quotes: bool
    """Whether the reply gives this field's quotations of the client apart from its text."""

    @property
    def path(self) -> str:
        return f"{self.section}.{self.field.key}"


def section_call_fields(definition: NoteTypeDefinition, call: str) -> list[SectionField]:
    """The fields ``call`` drafts for ``definition``, in note order.

    None for a type written in code, which has no spec.
    """
    if definition.source_spec is None:
        return []
    return [
        SectionField(
            section.key,
            f,
            quotes=(section.key in QUOTING_SECTIONS or f.key in QUOTING_FIELDS)
            and f.kind == "text",
        )
        for section in definition.sections
        if SECTION_CALLS.get(section.key) == call
        for f in section.fields
        if f.source is None and f.kind in _ROUTED_KINDS
    ]


def without_fields(
    definition: NoteTypeDefinition, fields: Collection[SectionField]
) -> NoteTypeDefinition:
    """The definition less ``fields``, and less a section with nothing left in it."""
    dropped = {(f.section, f.field.key) for f in fields}
    sections = []
    for section in definition.sections:
        kept = tuple(f for f in section.fields if (section.key, f.key) not in dropped)
        if kept:
            sections.append(replace(section, fields=kept))
    return replace(definition, sections=tuple(sections))
