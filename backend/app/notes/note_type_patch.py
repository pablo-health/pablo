# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Checking and applying a based note type's patch to its base.

A practice type can be a base plus a patch rather than a copy: the parts it
adds, hides or relabels, and instructions it appends. The patch is applied
whenever the type is read, never stored as a copy, so an improvement to the
base reaches every type built on it.

Two functions, both pure:

- :func:`check_patch` is strict and runs when a type is saved or tried:
  every path must name a part of the base, added keys must not collide, a
  field the base requires cannot be hidden.
- :func:`resolve_spec` is lenient and runs on every read. A base can change
  under a stored patch (by deploy, or when a practice edits a type others
  are based on), and a note written last month must still render. So a
  reference to a part the base no longer has is skipped, an anchor that is
  gone places the addition at the end, a part the base has since gained
  wins over an addition with the same key, and a field the base has since
  made required stays visible.

What a patch cannot do is structural: :class:`~.practice_spec.PartOverride`
has no ``kind``, and the appended instructions go after the base's prompt
and before the generation floor, which the definition always appends last.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from .practice_spec import (
    NoteTypePatch,
    PartOverride,
    PracticeFieldSpec,
    PracticeInputSpec,
    PracticeNoteTypeSpec,
    PracticeSectionSpec,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

PatchProblem = tuple[tuple[str | int, ...], str]
"""Where in the patch (``("hide_fields", 0)``) and what is wrong there."""

_ADDED_INPUTS_HEADER = "Also supplied for this visit:"


class PatchError(ValueError):
    """A patch that does not fit its base; ``problems`` locates each one."""

    def __init__(self, problems: list[PatchProblem]) -> None:
        super().__init__("; ".join(message for _, message in problems))
        self.problems = problems


def check_patch(
    base: PracticeNoteTypeSpec,
    patch: NoteTypePatch,
    required_fields: Collection[str] = (),
) -> None:
    """Raise :class:`PatchError` unless every part of ``patch`` fits ``base``."""
    problems = [
        *_hiding_problems(base, patch, required_fields),
        *_override_problems(base, patch),
        *_addition_problems(base, patch),
    ]
    if problems:
        raise PatchError(problems)
    try:
        resolve_spec(base, patch, required_fields)
    except ValidationError as exc:
        raise PatchError(
            [((), f"the adjusted type is not valid: {e['msg']}") for e in exc.errors()]
        ) from exc


def _hiding_problems(
    base: PracticeNoteTypeSpec, patch: NoteTypePatch, required_fields: Collection[str]
) -> Iterator[PatchProblem]:
    sections = {s.key: s for s in base.sections}
    fields = {f"{s.key}.{f.key}" for s in base.sections for f in s.fields}
    for i, key in enumerate(patch.hide_sections):
        if key not in sections:
            yield ("hide_sections", i), f"{key!r} is not a section of the base"
            continue
        held = [p for p in (f"{key}.{f.key}" for f in sections[key].fields) if p in required_fields]
        if held:
            yield (
                ("hide_sections", i),
                f"{key!r} holds {held[0]!r}, which the base requires; it cannot be hidden",
            )
    for i, path in enumerate(patch.hide_fields):
        if path not in fields:
            yield ("hide_fields", i), f"{path!r} is not a field of the base"
        elif path in required_fields:
            yield ("hide_fields", i), f"{path!r} is required by the base and cannot be hidden"
    hidden_fields = set(patch.hide_fields)
    for key, section in sections.items():
        if key in patch.hide_sections:
            continue
        if all(f"{key}.{f.key}" in hidden_fields for f in section.fields):
            yield (
                ("hide_fields",),
                f"hiding every field of {key!r} leaves it empty; hide the section instead",
            )


def _override_problems(base: PracticeNoteTypeSpec, patch: NoteTypePatch) -> Iterator[PatchProblem]:
    sections = {s.key for s in base.sections}
    fields = {f"{s.key}.{f.key}" for s in base.sections for f in s.fields}
    for i, override in enumerate(patch.override):
        if override.path not in sections and override.path not in fields:
            yield ("override", i, "path"), f"{override.path!r} is not a part of the base"
        elif override.path in sections and override.ai_hint is not None:
            yield ("override", i, "ai_hint"), "a section has no hint; adjust its fields instead"


def _addition_problems(base: PracticeNoteTypeSpec, patch: NoteTypePatch) -> Iterator[PatchProblem]:
    sections = {s.key: s for s in base.sections}
    for i, added in enumerate(patch.add_sections):
        if added.section.key in sections:
            yield (
                ("add_sections", i, "section", "key"),
                f"section key {added.section.key!r} is already in the base",
            )
        if added.after is not None and added.after not in sections:
            yield ("add_sections", i, "after"), f"{added.after!r} is not a section of the base"
    for i, added_field in enumerate(patch.add_fields):
        section = sections.get(added_field.section)
        if section is None:
            yield (
                ("add_fields", i, "section"),
                f"{added_field.section!r} is not a section of the base",
            )
            continue
        if section.key in patch.hide_sections:
            yield (
                ("add_fields", i, "section"),
                f"{section.key!r} is hidden; a field cannot be added to it",
            )
        keys = {f.key for f in section.fields}
        if added_field.field.key in keys:
            yield (
                ("add_fields", i, "field", "key"),
                f"field key {added_field.field.key!r} is already in section {section.key!r}",
            )
        if added_field.after is not None and added_field.after not in keys:
            yield (
                ("add_fields", i, "after"),
                f"{added_field.after!r} is not a field of section {section.key!r}",
            )
    base_inputs = {i.key for i in base.inputs}
    for i, added_input in enumerate(patch.add_inputs):
        if added_input.key in base_inputs:
            yield ("add_inputs", i, "key"), f"input key {added_input.key!r} is already in the base"


def resolve_spec(
    base: PracticeNoteTypeSpec,
    patch: NoteTypePatch,
    required_fields: Collection[str] = (),
) -> PracticeNoteTypeSpec:
    """``base`` with ``patch`` applied, as a full spec. Lenient: see the module docstring."""
    hidden_sections = set(patch.hide_sections) - {p.split(".", 1)[0] for p in required_fields}
    hidden_fields = set(patch.hide_fields) - set(required_fields)
    overrides = {o.path: o for o in patch.override}

    sections: list[PracticeSectionSpec] = []
    for section in base.sections:
        if section.key in hidden_sections:
            continue
        kept = [
            _overridden(f, overrides.get(f"{section.key}.{f.key}"))
            for f in section.fields
            if f"{section.key}.{f.key}" not in hidden_fields
        ]
        added = [(a.field, a.after) for a in patch.add_fields if a.section == section.key]
        merged = _placed(kept, added)
        if not merged:
            continue
        override = overrides.get(section.key)
        sections.append(
            section.model_copy(
                update={
                    "label": override.label if override and override.label else section.label,
                    "fields": merged,
                }
            )
        )
    sections = _placed(sections, [(a.section, a.after) for a in patch.add_sections])

    base_input_keys = {i.key for i in base.inputs}
    added_inputs = [i for i in patch.add_inputs if i.key not in base_input_keys]
    return PracticeNoteTypeSpec(
        label=base.label,
        description=base.description,
        system_prompt=_appended(base.system_prompt, patch.system_prompt_append),
        user_template=_with_added_inputs(base.user_template, added_inputs),
        sections=sections,
        inputs=[*base.inputs, *added_inputs],
    )


def _overridden(field: PracticeFieldSpec, override: PartOverride | None) -> PracticeFieldSpec:
    if override is None:
        return field
    update: dict[str, str] = {}
    if override.label is not None:
        update["label"] = override.label
    if override.ai_hint is not None:
        update["ai_hint"] = override.ai_hint
    return field.model_copy(update=update)


def _placed[P: (PracticeFieldSpec, PracticeSectionSpec)](
    parts: list[P], additions: list[tuple[P, str | None]]
) -> list[P]:
    """``parts`` with each addition after its anchor (in patch order), or at the end.

    An addition whose key the parts already have is dropped: the base gained
    that part since the patch was written, and the base's wins.
    """
    placed = list(parts)
    last_after: dict[str, str] = {}
    for part, after in additions:
        keys = [p.key for p in placed]
        if part.key in keys:
            continue
        anchor = last_after.get(after, after) if after is not None else None
        if anchor is None or anchor not in keys:
            placed.append(part)
        else:
            placed.insert(keys.index(anchor) + 1, part)
        if after is not None:
            last_after[after] = part.key
    return placed


def _appended(system_prompt: str, append: str | None) -> str:
    if not append or not append.strip():
        return system_prompt
    if not system_prompt.strip():
        return append.strip()
    return f"{system_prompt.rstrip()}\n\n{append.strip()}"


def _with_added_inputs(user_template: str | None, added: list[PracticeInputSpec]) -> str | None:
    """A base template places only its own inputs, so added ones get a block of their own.

    It goes just before the paragraph that holds the transcript, so the
    model reads every entered value before the visit itself. With no
    template the default layout already lists every input.
    """
    if user_template is None or not added:
        return user_template
    block = "\n".join([_ADDED_INPUTS_HEADER, *(f"- {i.label}: {{inputs.{i.key}}}" for i in added)])
    paragraph = user_template.rfind("\n\n", 0, user_template.index("{transcript}"))
    if paragraph == -1:
        return f"{block}\n\n{user_template}"
    return f"{user_template[:paragraph]}\n\n{block}{user_template[paragraph:]}"
