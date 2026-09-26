# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: data types and in-memory registry.

The registry is the single source of truth for what note formats exist,
what sections they have, and what fields live inside each section. The
generation service, API surface, and frontend all drive off the registry
so adding a new note type is a configuration change, not a code change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from ..models import Patient, Transcript

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


NoteInputKind = Literal["text", "choice"]
"""Shape of a generate-time input: free text, or one of a fixed set of options."""


@dataclass(frozen=True)
class NoteInputDef:
    """A value the clinician supplies when a note is generated.

    Inputs carry context the transcript cannot: which hypothesis a call is
    testing, which program a client is enrolled in. They are stored on the
    note, so a regenerate reuses them.
    """

    key: str
    label: str
    kind: NoteInputKind = "text"
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class NoteSectionDef:
    """A section inside a note type (e.g. SOAP's ``subjective``)."""

    key: str
    label: str
    fields: tuple[NoteFieldDef, ...]

    def field_keys(self) -> list[str]:
        return [f.key for f in self.fields]


PromptBuilder = Callable[["NoteTypeDefinition", "Transcript", "Patient", datetime], str]
"""Optional hook on a :class:`NoteTypeDefinition` to override prompt synthesis.

The default generator builds a prompt from each section/field's ``ai_hint``
— fine for note types where the hints capture all the nuance. SOAP (and
likely future formats with strong clinical conventions) uses a hand-tuned
prompt instead; the builder takes the definition plus the same generation
inputs (transcript, patient, session date) and returns the full user
prompt string. The system prompt is supplied separately by the service
that invokes the gateway.
"""


@dataclass(frozen=True)
class NoteTypeDefinition:
    """Top-level note format (e.g. SOAP, Narrative, DAP)."""

    key: str
    label: str
    description: str
    sections: tuple[NoteSectionDef, ...]
    tier: NoteTier = "core"
    context: NoteContext = "session"
    prompt_builder: PromptBuilder | None = field(default=None, compare=False)
    """If set, used instead of the auto-built ``ai_hint``-based prompt.

    Opt-in per note type — DAP/BIRP/etc start with the default and
    graduate to a custom builder if/when prompt nuance requires it.
    """
    system_prompt: str | None = field(default=None, compare=False)
    """If set, used as the system prompt instead of the shared default.

    The default system prompt frames every transcript as a therapy
    session. Note types with a different shape (a practice-level review,
    say) can override it here instead of fighting that framing from
    inside the user prompt.
    """
    inputs: tuple[NoteInputDef, ...] = ()
    user_template: str | None = field(default=None, compare=False)
    """If set, rendered as the user prompt (see :mod:`app.notes.practice_types`).

    Practice-defined types carry a template rather than a ``prompt_builder``
    because a stored definition cannot carry code.
    """
    version: int | None = None
    """Definition version. ``None`` for built-in types, which change only by deploy."""

    def section_keys(self) -> list[str]:
        return [s.key for s in self.sections]


class PracticeNoteTypeSource(Protocol):
    """Where the current practice's own note types come from.

    Reads are scoped by the caller's tenant session, so one practice's
    types are never visible to another.
    """

    def get(self, key: str, version: int | None = None) -> NoteTypeDefinition | None:
        """Return the definition (latest version when ``version`` is None), or None."""

    def is_active(self, key: str) -> bool:
        """Whether ``key`` exists and has not been retired."""

    def all_active(self) -> list[NoteTypeDefinition]:
        """Latest version of every type the practice has not retired."""


PRACTICE_KEY_PREFIX = "custom."
"""Every practice-defined key starts with this, so it can never shadow a built-in."""


def is_practice_key(key: str) -> bool:
    return key.startswith(PRACTICE_KEY_PREFIX)


class NoteTypeRegistry:
    """Built-in note types, plus the current practice's own when a source is set.

    Built-in definitions live in memory and are registered at startup.
    Keys under :data:`PRACTICE_KEY_PREFIX` are looked up in the practice
    source instead, which reads the caller's tenant schema — so every
    consumer that asks the registry sees the same set a practice does,
    without knowing which kind of type it holds.

    Not thread-safe for mutation — registration and the source are set at
    startup only; reads after that point are safe for concurrent use.
    """

    def __init__(self) -> None:
        self._types: dict[str, NoteTypeDefinition] = {}
        self._practice_source: PracticeNoteTypeSource | None = None

    def register(
        self,
        definition: NoteTypeDefinition,
        *,
        replace: bool = False,
    ) -> None:
        """Register a built-in note type.

        Raises :class:`ValueError` if a type with the same key is already
        registered (unless ``replace=True``), or if the key is in the
        practice namespace.
        """
        if is_practice_key(definition.key):
            raise ValueError(f"{PRACTICE_KEY_PREFIX!r} keys belong to practices, not built-ins")
        existing = self._types.get(definition.key)
        if existing is not None and not replace:
            raise ValueError(f"Note type {definition.key!r} is already registered")
        self._types[definition.key] = definition

    def set_practice_source(self, source: PracticeNoteTypeSource | None) -> None:
        """Resolve practice-namespace keys through ``source`` (``None`` turns it off)."""
        self._practice_source = source

    def get(self, key: str, version: int | None = None) -> NoteTypeDefinition:
        """Return the definition for ``key`` or raise :class:`KeyError`.

        A practice type resolves to its latest version, retired or not, so a
        note written against it still renders; ``version`` picks an earlier
        one. Built-in types have one version and ignore it.
        """
        if is_practice_key(key):
            found = self._practice_source.get(key, version) if self._practice_source else None
            if found is None:
                raise KeyError(f"Note type {key!r} is not registered")
            return found
        try:
            return self._types[key]
        except KeyError as exc:
            raise KeyError(f"Note type {key!r} is not registered") from exc

    def has(self, key: str) -> bool:
        """Whether a new note may be created with ``key`` — retired types may not."""
        if is_practice_key(key):
            return self._practice_source is not None and self._practice_source.is_active(key)
        return key in self._types

    def all(self) -> list[NoteTypeDefinition]:
        """Built-ins plus the practice's active types, sorted by key."""
        definitions = list(self._types.values())
        if self._practice_source is not None:
            definitions.extend(self._practice_source.all_active())
        return sorted(definitions, key=lambda d: d.key)

    def keys(self) -> list[str]:
        return [d.key for d in self.all()]

    def clear(self) -> None:
        """Drop all registrations and the practice source. Intended for tests only."""
        self._types.clear()
        self._practice_source = None


_DEFAULT_REGISTRY: NoteTypeRegistry = NoteTypeRegistry()


def get_default_registry() -> NoteTypeRegistry:
    """Return the process-wide default registry.

    Pablo populates this with SOAP + Narrative at startup. Downstream
    overlays may register additional formats against the same instance
    at bootstrap.
    """
    return _DEFAULT_REGISTRY
