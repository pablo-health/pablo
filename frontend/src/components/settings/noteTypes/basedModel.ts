// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The editor's working copy of a note type built on a base: the base as it
 * is, plus what the practice hides, adds and asks for in its own words.
 *
 * Only the changes are saved (`{base, patch}`); the server applies them to
 * the base each time it reads the type, so the type keeps up with the base.
 * `shapeOf` applies them here too, but only so Try it can lay a draft out in
 * the type's shape — what is saved and what drafts is the server's work.
 */

import type {
  BasedOn,
  NoteTypeBase,
  NoteTypePatch,
  PracticeFieldSpec,
  PracticeNoteTypeSpec,
  PracticeSectionSpec,
} from "@/types/noteTypes"
import {
  draftField,
  draftFromSpec,
  specFromDraft,
  withKeys,
  type DraftField,
  type DraftInput,
  type DraftSection,
} from "./editorModel"

/** A field the practice adds to a base section. `after` is carried from a saved patch; new ones go last. */
export interface AddedDraftField extends DraftField {
  after: string | null
}

export interface BasedDraft {
  /** The saved type's slug; null until a new type is first saved. */
  slug: string | null
  preferredSlug?: string
  label: string
  description: string
  base: NoteTypeBase
  hiddenFields: string[]
  hiddenSections: string[]
  /** By base section key. */
  addedFields: Record<string, AddedDraftField[]>
  addedSections: DraftSection[]
  /** Where each added section goes, by its uid; absent means last. */
  sectionAnchors: Record<string, string | null>
  addedInputs: DraftInput[]
  /** Not edited here; a saved patch's relabels are carried through unchanged. */
  overrides: NoteTypePatch["override"]
  instructions: string
}

const EMPTY_SPEC: PracticeNoteTypeSpec = {
  label: "",
  description: "",
  system_prompt: "",
  user_template: null,
  sections: [],
  inputs: [],
}

const EMPTY_PATCH: NoteTypePatch = {
  add_sections: [],
  add_fields: [],
  hide_fields: [],
  hide_sections: [],
  override: [],
  add_inputs: [],
  system_prompt_append: null,
}

/** A based draft: a fresh one on `base`, or a saved type's `spec` on it. */
export function basedDraftFrom(
  base: NoteTypeBase,
  options: { spec?: PracticeNoteTypeSpec; slug?: string | null; preferredSlug?: string } = {},
): BasedDraft {
  const patch = { ...EMPTY_PATCH, ...options.spec?.patch }
  // The full-spec editor's draft gives every added part a uid.
  const parts = draftFromSpec(
    { ...EMPTY_SPEC, sections: patch.add_sections.map((a) => a.section), inputs: patch.add_inputs },
    null,
  )
  const addedFields: Record<string, AddedDraftField[]> = {}
  for (const added of patch.add_fields) {
    ;(addedFields[added.section] ??= []).push({ ...draftField(added.field), after: added.after })
  }
  return {
    slug: options.slug ?? null,
    preferredSlug: options.preferredSlug,
    label: options.spec?.label ?? base.label,
    description: options.spec?.description ?? base.description,
    base,
    hiddenFields: [...patch.hide_fields],
    hiddenSections: [...patch.hide_sections],
    addedFields,
    addedSections: parts.sections,
    sectionAnchors: Object.fromEntries(parts.sections.map((s, i) => [s.uid, patch.add_sections[i].after])),
    addedInputs: parts.inputs,
    overrides: patch.override,
    instructions: patch.system_prompt_append ?? "",
  }
}

/** The body the save and preview routes take: the base and the changes, nothing else. */
export function specFromBasedDraft(draft: BasedDraft): PracticeNoteTypeSpec {
  const base = draft.base.spec
  // Added parts are keyed like any other part, never colliding with the base's own.
  const added = specFromDraft({
    ...EMPTY_SPEC,
    slug: null,
    sections: withKeys(draft.addedSections, "section", base.sections.map((s) => s.key)),
    inputs: withKeys(draft.addedInputs, "input", base.inputs.map((i) => i.key)),
  })
  const addFields = base.sections.flatMap((section) =>
    withKeys(draft.addedFields[section.key] ?? [], "field", section.fields.map((f) => f.key)).map((f) => ({
      section: section.key,
      field: { key: f.key, label: f.label, kind: f.kind, ai_hint: f.ai_hint },
      after: f.after,
    })),
  )
  return {
    ...EMPTY_SPEC,
    label: draft.label,
    description: draft.description,
    base: draft.base.key,
    patch: {
      add_sections: added.sections.map((section, i) => ({
        section,
        after: draft.sectionAnchors[draft.addedSections[i].uid] ?? null,
      })),
      add_fields: addFields,
      hide_fields: draft.hiddenFields,
      hide_sections: draft.hiddenSections,
      override: draft.overrides,
      add_inputs: added.inputs,
      system_prompt_append: draft.instructions.trim() ? draft.instructions : null,
    },
  }
}

/** Whether a base field (`section.field`) may be hidden. */
export function canHide(base: NoteTypeBase, path: string): boolean {
  return !base.required_fields.includes(path)
}

/** Whether a base section may be hidden: none of its fields is required. */
export function canHideSection(base: NoteTypeBase, section: PracticeSectionSpec): boolean {
  return section.fields.every((f) => canHide(base, `${section.key}.${f.key}`))
}

/** The note's shape with the changes applied, for laying out a Try it draft. */
export function shapeOf(spec: PracticeNoteTypeSpec, base: NoteTypeBase): PracticeNoteTypeSpec {
  const patch = spec.patch ?? EMPTY_PATCH
  const relabel = new Map(patch.override.map((o) => [o.path, o]))
  const sections = base.spec.sections
    .filter((s) => !patch.hide_sections.includes(s.key))
    .map((s) => ({
      ...s,
      label: relabel.get(s.key)?.label || s.label,
      fields: placed(
        s.fields
          .filter((f) => !patch.hide_fields.includes(`${s.key}.${f.key}`))
          .map((f) => {
            const o = relabel.get(`${s.key}.${f.key}`)
            return { ...f, label: o?.label || f.label, ai_hint: o?.ai_hint ?? f.ai_hint }
          }),
        patch.add_fields.filter((a) => a.section === s.key).map((a) => [a.field, a.after] as const),
      ),
    }))
    .filter((s) => s.fields.length > 0)
  return {
    ...base.spec,
    label: spec.label,
    description: spec.description,
    sections: placed(sections, patch.add_sections.map((a) => [a.section, a.after] as const)),
    inputs: [...base.spec.inputs, ...patch.add_inputs],
  }
}

function placed<P extends PracticeFieldSpec | PracticeSectionSpec>(
  parts: P[],
  additions: (readonly [P, string | null])[],
): P[] {
  const out = [...parts]
  const lastAfter = new Map<string, string>()
  for (const [part, after] of additions) {
    const anchor = after === null ? null : (lastAfter.get(after) ?? after)
    const at = anchor === null ? -1 : out.findIndex((p) => p.key === anchor)
    if (at === -1) out.push(part)
    else out.splice(at + 1, 0, part)
    if (after !== null) lastAfter.set(after, part.key)
  }
  return out
}

/** "Based on X; 2 additions, 1 hidden" — the list's line for a based type. */
export function basedOnLine(basedOn: BasedOn): string {
  const additions = `${basedOn.additions} ${basedOn.additions === 1 ? "addition" : "additions"}`
  return `Based on ${basedOn.label}; ${additions}, ${basedOn.hidden} hidden`
}
