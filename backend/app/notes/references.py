# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""References a derived note type can be compared against.

A reference is a named list of elements a note of some kind is expected to
carry. When a practice derives a note type from its own notes, naming a
reference lists the elements the proposal lacks, as suggestions the
clinician may take or leave; nothing is added on their behalf.

Any note type in the registry serves as a reference, its sections being
the elements. A deployment can also register references of its own with
:func:`register_note_type_reference`, configurable per deployment; a
registered key takes precedence over a note type with the same key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .practice_types import PracticeNoteTypeSpec
    from .registry import NoteTypeDefinition


@dataclass(frozen=True)
class RequiredElement:
    """One element a reference expects.

    ``terms`` are other names the element goes by. The element is present
    in a proposal when its label or any term appears among the proposal's
    section and field names.
    """

    label: str
    terms: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class NoteTypeReference:
    key: str
    label: str
    required_elements: tuple[RequiredElement, ...] = field(default_factory=tuple)


_registered: dict[str, NoteTypeReference] = {}


def register_note_type_reference(
    key: str, label: str, required_elements: Iterable[RequiredElement]
) -> None:
    """Make ``key`` available as a reference; registering a key again replaces it."""
    elements = tuple(required_elements)
    if not key or not elements:
        raise ValueError("a reference needs a key and at least one element")
    _registered[key] = NoteTypeReference(key=key, label=label, required_elements=elements)


def registered_note_type_references() -> list[NoteTypeReference]:
    """The references registered with this deployment, sorted by key."""
    return [_registered[key] for key in sorted(_registered)]


def get_registered_note_type_reference(key: str) -> NoteTypeReference | None:
    return _registered.get(key)


def reference_from_definition(definition: NoteTypeDefinition) -> NoteTypeReference:
    """A note type as a reference: each of its sections is an element."""
    return NoteTypeReference(
        key=definition.key,
        label=definition.label,
        required_elements=tuple(
            RequiredElement(
                label=section.label,
                terms=(section.key.replace("_", " "),),
                description=", ".join(f.label for f in section.fields),
            )
            for section in definition.sections
        ),
    )


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    width = len(needle)
    return width > 0 and any(
        haystack[i : i + width] == needle for i in range(len(haystack) - width + 1)
    )


def missing_elements(
    reference: NoteTypeReference, spec: PracticeNoteTypeSpec
) -> list[RequiredElement]:
    """The reference's elements that no section or field of ``spec`` names."""
    names = [_words(s.label) for s in spec.sections]
    names += [_words(s.key.replace("_", " ")) for s in spec.sections]
    for section in spec.sections:
        names += [_words(f.label) for f in section.fields]
        names += [_words(f.key.replace("_", " ")) for f in section.fields]
    return [
        element
        for element in reference.required_elements
        if not any(
            _contains(name, _words(term))
            for term in (element.label, *element.terms)
            for name in names
        )
    ]


def clear_registered_note_type_references() -> None:
    """Forget every registered reference. For tests."""
    _registered.clear()
