# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: data types and in-memory registry.

The registry is the single source of truth for what note formats exist,
what sections they have, and what fields live inside each section. The
generation service, API surface, and frontend all drive off the registry
so adding a new note type is a configuration change, not a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NoteFieldKind = Literal["text", "list", "structured"]
"""Shape of a single field within a section.

- ``text``: free-form paragraph (e.g. SOAP's ``chief_complaint``)
- ``list``: ordered list of short items (e.g. SOAP's ``interventions_used``)
- ``structured``: nested schema (reserved for richer future fields)
"""

NoteTier = Literal["core", "extension"]
"""Tier gating for a note type.

``core`` types are registered by Pablo at startup and always available.
``extension`` types are registered by a downstream overlay (e.g. a
distributor that adds proprietary formats) and may be gated by that
overlay's own access logic.
"""

NoteContext = Literal["session", "patient", "practice"]
"""Lifecycle context for a note type.

- ``session``: bound to one session (one-to-one with a session record).
  SOAP, Narrative, DAP, BIRP, GIRP — anything generated from a session
  transcript or written about a single visit.
- ``patient``: bound to one patient, independent of any single session.
  Versioned over time. Examples: safety plan (Stanley-Brown), intake,
  treatment plan. A session note may reference the current patient-context
  document but does not own its lifecycle.
- ``practice``: bound to clinic-level workflows, not to a specific
  patient or session. Examples: supervision case reviews, multi-clinician
  audit notes.

The context field shapes both storage (which foreign key the note hangs
off of) and UX (where the "create note" entry point lives).
"""


@dataclass(frozen=True)
class NoteFieldDef:
    """A single field inside a section (e.g. ``chief_complaint``)."""

    key: str
    label: str
    kind: NoteFieldKind
    ai_hint: str = ""


@dataclass(frozen=True)
class NoteSectionDef:
    """A section inside a note type (e.g. SOAP's ``subjective``)."""

    key: str
    label: str
    fields: tuple[NoteFieldDef, ...]

    def field_keys(self) -> list[str]:
        return [f.key for f in self.fields]


@dataclass(frozen=True)
class NoteTypeDefinition:
    """Top-level note format (e.g. SOAP, Narrative, DAP)."""

    key: str
    label: str
    description: str
    sections: tuple[NoteSectionDef, ...]
    tier: NoteTier = "core"
    context: NoteContext = "session"

    def section_keys(self) -> list[str]:
        return [s.key for s in self.sections]


class NoteTypeRegistry:
    """In-memory map of note-type key to definition.

    Not thread-safe — mutations are expected at import/startup time only;
    reads after that point are safe for concurrent use.
    """

    def __init__(self) -> None:
        self._types: dict[str, NoteTypeDefinition] = {}

    def register(
        self,
        definition: NoteTypeDefinition,
        *,
        replace: bool = False,
    ) -> None:
        """Register a note type.

        Raises :class:`ValueError` if a type with the same key is already
        registered, unless ``replace=True``.
        """
        existing = self._types.get(definition.key)
        if existing is not None and not replace:
            raise ValueError(f"Note type {definition.key!r} is already registered")
        self._types[definition.key] = definition

    def get(self, key: str) -> NoteTypeDefinition:
        """Return the definition for ``key`` or raise :class:`KeyError`."""
        try:
            return self._types[key]
        except KeyError as exc:
            raise KeyError(f"Note type {key!r} is not registered") from exc

    def has(self, key: str) -> bool:
        return key in self._types

    def all(self) -> list[NoteTypeDefinition]:
        """All registered definitions, sorted by key for stable ordering."""
        return [self._types[k] for k in sorted(self._types)]

    def keys(self) -> list[str]:
        return sorted(self._types)

    def clear(self) -> None:
        """Drop all registrations. Intended for tests only."""
        self._types.clear()


_DEFAULT_REGISTRY: NoteTypeRegistry = NoteTypeRegistry()


def get_default_registry() -> NoteTypeRegistry:
    """Return the process-wide default registry.

    Pablo populates this with SOAP + Narrative at startup. Downstream
    overlays may register additional formats against the same instance
    at bootstrap.
    """
    return _DEFAULT_REGISTRY
