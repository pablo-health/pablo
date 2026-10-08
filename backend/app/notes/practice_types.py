# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note types a practice defines for itself.

A practice-defined type is data, not code: sections and fields with their
generation hints, a system prompt, an optional user-prompt template, and
the inputs a clinician supplies when a note is generated. It is stored in
the practice's own schema, so one practice never sees another's types, and
resolved alongside the built-in registry.

A type can also be a base plus a patch (:mod:`.note_type_patch`): another
spec-shaped type with parts added, hidden or relabelled. It is resolved
against the base each time it is read, so it keeps up with the base.

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

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .note_type_patch import PatchError, check_patch, resolve_spec
from .practice_spec import PracticeNoteTypeSpec
from .registry import (
    PRACTICE_KEY_PREFIX,
    BasedOn,
    NoteFieldDef,
    NoteInputDef,
    NoteSectionDef,
    NoteTypeDefinition,
    get_default_registry,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from ..models import Transcript
    from ..repositories.practice_note_type import PracticeNoteTypeRepository, StoredNoteType

logger = logging.getLogger(__name__)

SLUG_PATTERN = r"^[a-z][a-z0-9_]{0,22}$"
"""A practice key is ``custom.<slug>``; the note_type column holds 30 characters."""

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

_PLACEHOLDER = re.compile(r"\{(transcript|session_date|fields|chart|inputs\.[a-z][a-z0-9_]*)\}")

type BaseLookup = Callable[[str], NoteTypeDefinition | None]
"""Finds the definition a based type names (``NoteTypeRegistry.base_for``)."""


def with_generation_floor(system_prompt: str) -> str:
    """The practice's system prompt (or a neutral default) with the floor after it."""
    return f"{system_prompt.strip() or _DEFAULT_SYSTEM_PROMPT}\n\n{GENERATION_FLOOR}"


def practice_key(slug: str) -> str:
    return f"{PRACTICE_KEY_PREFIX}{slug}"


def to_definition(
    key: str,
    version: int | None,
    spec: PracticeNoteTypeSpec,
    *,
    required_fields: tuple[str, ...] = (),
) -> NoteTypeDefinition:
    """Build the registry definition a full spec generates with.

    A based spec has no sections of its own; :func:`resolve` builds those.
    """
    if spec.base is not None:
        raise ValueError(f"{key!r} is based on {spec.base!r}; resolve it against its base")
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
        required_fields=required_fields,
        source_spec=spec,
        full_chart=True,
    )


def resolve(
    key: str, version: int | None, base: NoteTypeDefinition, spec: PracticeNoteTypeSpec
) -> NoteTypeDefinition:
    """The definition of a based ``spec``: its base with its patch applied.

    The practice's own name and description stand; everything else comes from
    the base as it is now. Pure — nothing is read or written.
    """
    patch = spec.patch
    if base.source_spec is None or patch is None:
        raise ValueError(f"{key!r} needs a spec-shaped base and a patch")
    resolved = resolve_spec(base.source_spec, patch, base.required_fields)
    full = resolved.model_copy(update={"label": spec.label, "description": spec.description})
    return replace(
        to_definition(key, version, full, required_fields=base.required_fields),
        full_chart=base.full_chart,
        based_on=BasedOn(
            key=base.key, label=base.label, additions=patch.additions(), hidden=patch.hidden()
        ),
    )


def check_against_base(spec: PracticeNoteTypeSpec, bases: BaseLookup) -> None:
    """Raise :class:`PatchError` unless a based ``spec`` fits the base it names.

    A full spec has nothing to check here; its own validation already ran.
    """
    if spec.base is None or spec.patch is None:
        return
    base = bases(spec.base)
    if base is None or base.source_spec is None:
        raise PatchError([(("base",), f"there is no note type {spec.base!r} to adjust")])
    check_patch(base.source_spec, spec.patch, base.required_fields)


def definition_for(
    key: str, version: int | None, spec: PracticeNoteTypeSpec, bases: BaseLookup
) -> NoteTypeDefinition:
    """The definition ``spec`` generates with, resolving it against its base if it has one.

    Raises :class:`LookupError` when the base it names no longer exists.
    """
    if spec.base is None:
        return to_definition(key, version, spec)
    base = bases(spec.base)
    if base is None:
        raise LookupError(f"{key!r} is based on {spec.base!r}, which is not a note type now")
    return resolve(key, version, base, spec)


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


@dataclass(frozen=True)
class PromptBlocks:
    """Prompt text the generation service composes for a template to place.

    ``fields`` enumerates the type's sections and fields; ``chart`` is the
    client's problem list and allergies, ``None`` when there is no client.
    """

    fields: str
    chart: str | None = None


def render_user_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    session_date: datetime,
    inputs: Mapping[str, str],
    blocks: PromptBlocks,
) -> str:
    """Render the type's template, or a default layout when it has none.

    Only the known placeholders are substituted, so any other braces a
    practice writes (a JSON example, say) pass through untouched. The chart
    goes where a template puts ``{chart}``; a template written before there
    was a chart to place gets it ahead of everything else, so no type drafts
    without it.
    """
    date = session_date.isoformat().split("T", 1)[0]
    if definition.user_template is None:
        lines = [f"Produce a {definition.label} note.", "", blocks.fields, ""]
        lines.extend(_inputs_lines(definition, inputs))
        if blocks.chart:
            lines.extend([blocks.chart, ""])
        lines.extend([f"Session date: {date}", "", "Transcript:", transcript.content])
        return "\n".join(lines)

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "transcript":
            return transcript.content
        if name == "session_date":
            return date
        if name == "fields":
            return blocks.fields
        if name == "chart":
            return blocks.chart or "Chart: not available."
        return inputs.get(name.removeprefix("inputs."), "not provided")

    rendered = _PLACEHOLDER.sub(substitute, definition.user_template)
    if blocks.chart and "{chart}" not in definition.user_template:
        return f"{blocks.chart}\n\n{rendered}"
    return rendered


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
    A based type is resolved against ``bases`` on every read; without it,
    against the process-wide registry.
    """

    def __init__(
        self,
        repository: Callable[[], PracticeNoteTypeRepository],
        bases: BaseLookup | None = None,
    ) -> None:
        self._repository = repository
        self._bases = bases

    def _base_lookup(self) -> BaseLookup:
        return self._bases or get_default_registry().base_for

    def get(self, key: str, version: int | None = None) -> NoteTypeDefinition | None:
        stored = self._repository().get(key, version)
        if stored is None:
            return None
        try:
            return stored_to_definition(stored, self._base_lookup())
        except LookupError:
            logger.warning("Practice note type %s names a base that is gone", key)
            return None

    def is_active(self, key: str) -> bool:
        stored = self._repository().get(key)
        return stored is not None and stored.retired_at is None

    def all_active(self) -> list[NoteTypeDefinition]:
        bases = self._base_lookup()
        definitions = []
        for stored in self._repository().list_latest():
            if stored.retired_at is not None:
                continue
            try:
                definitions.append(stored_to_definition(stored, bases))
            except LookupError:
                logger.warning("Practice note type %s names a base that is gone", stored.key)
        return definitions


def stored_to_definition(stored: StoredNoteType, bases: BaseLookup) -> NoteTypeDefinition:
    spec = PracticeNoteTypeSpec.model_validate(stored.definition)
    return definition_for(stored.key, stored.version, spec, bases)
