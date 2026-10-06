// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The note-type editor's working copy, and the conversions to and from the
 * spec the API stores.
 *
 * A clinician names sections, fields and inputs; they never see a key. A part
 * loaded from a saved type or a template keeps the key it came with, so saving
 * it unchanged stores exactly what was loaded. A part added here gets a key
 * derived from its label when the type is saved.
 */

import { ApiError, type ValidationIssue } from "@/lib/api/client"
import type {
  NoteTypeSchema,
  PracticeFieldSpec,
  PracticeInputSpec,
  PracticeNoteTypeSpec,
  PracticeSectionSpec,
} from "@/types/noteTypes"

/** Section, field and input keys: `^[a-z][a-z0-9_]{0,39}$` on the server. */
const PART_KEY_MAX = 40
/** The slug after `custom.`: `^[a-z][a-z0-9_]{0,22}$`. */
const SLUG_MAX = 23

let nextUid = 0
const uid = () => `part-${++nextUid}`

export interface DraftField extends PracticeFieldSpec {
  uid: string
}
export interface DraftSection extends Omit<PracticeSectionSpec, "fields"> {
  uid: string
  fields: DraftField[]
}
export interface DraftInput extends PracticeInputSpec {
  uid: string
}
export interface NoteTypeDraft extends Omit<PracticeNoteTypeSpec, "sections" | "inputs"> {
  /** The saved type's slug; null until a new type is first saved. */
  slug: string | null
  /** A slug to try first for a new type (a template's own). */
  preferredSlug?: string
  sections: DraftSection[]
  inputs: DraftInput[]
}

export function draftFromSpec(
  spec: PracticeNoteTypeSpec,
  slug: string | null,
  preferredSlug?: string,
): NoteTypeDraft {
  return {
    ...spec,
    slug,
    preferredSlug,
    sections: spec.sections.map((s) => ({
      ...s,
      uid: uid(),
      fields: s.fields.map((f) => ({ ...f, uid: uid() })),
    })),
    inputs: spec.inputs.map((i) => ({ ...i, options: [...i.options], uid: uid() })),
  }
}

export const blankField = (): DraftField => ({ uid: uid(), key: "", label: "", kind: "text", ai_hint: "" })

export const blankSection = (): DraftSection => ({ uid: uid(), key: "", label: "", fields: [blankField()] })

export const blankInput = (): DraftInput => ({
  uid: uid(),
  key: "",
  label: "",
  kind: "text",
  options: [],
  required: false,
})

export function blankDraft(): NoteTypeDraft {
  return {
    slug: null,
    label: "",
    description: "",
    system_prompt: "",
    user_template: null,
    sections: [blankSection()],
    inputs: [],
  }
}

/** A lowercase identifier from a label: `Chief complaint` -> `chief_complaint`. */
export function keyFromLabel(label: string, max = PART_KEY_MAX): string {
  let key = label
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
  if (key && !/^[a-z]/.test(key)) key = `n_${key}`
  return key.slice(0, max).replace(/_+$/, "")
}

/** `base`, or `base_2`, `base_3`... — the first one not in `taken`, within `max`. */
function unique(base: string, taken: Set<string>, max: number): string {
  if (!taken.has(base)) return base
  for (let n = 2; ; n += 1) {
    const suffix = `_${n}`
    const candidate = `${base.slice(0, max - suffix.length)}${suffix}`
    if (!taken.has(candidate)) return candidate
  }
}

/** Keys for parts added in the editor, unique among their siblings. */
function withKeys<T extends { key: string; label: string }>(parts: T[], fallback: string): T[] {
  const taken = new Set(parts.map((p) => p.key).filter(Boolean))
  return parts.map((part, index) => {
    if (part.key) return part
    const key = unique(keyFromLabel(part.label) || `${fallback}_${index + 1}`, taken, PART_KEY_MAX)
    taken.add(key)
    return { ...part, key }
  })
}

/** The body the save and preview routes take. */
export function specFromDraft(draft: NoteTypeDraft): PracticeNoteTypeSpec {
  return {
    label: draft.label,
    description: draft.description,
    system_prompt: draft.system_prompt,
    user_template: draft.user_template,
    sections: withKeys(draft.sections, "section").map((s) => ({
      key: s.key,
      label: s.label,
      fields: withKeys(s.fields, "field").map((f) => ({
        key: f.key,
        label: f.label,
        kind: f.kind,
        ai_hint: f.ai_hint,
      })),
    })),
    inputs: withKeys(draft.inputs, "input").map((i) => ({
      key: i.key,
      label: i.label,
      kind: i.kind,
      // Edited one per line, so blank lines are dropped here rather than while typing.
      options: i.kind === "choice" ? i.options.map((o) => o.trim()).filter(Boolean) : [],
      required: i.required,
    })),
  }
}

/** The slug a type saves under: its own once saved, else one free for its name. */
export function slugFor(draft: NoteTypeDraft, takenKeys: string[]): string {
  if (draft.slug) return draft.slug
  const taken = new Set(takenKeys.map((k) => k.replace(/^custom\./, "")))
  const base = draft.preferredSlug || keyFromLabel(draft.label, SLUG_MAX) || "note_type"
  return unique(base, taken, SLUG_MAX)
}

/** The draft as a catalog definition, so a preview renders in its shape. */
export function definitionFromDraft(spec: PracticeNoteTypeSpec): NoteTypeSchema {
  return {
    key: "custom.preview",
    label: spec.label,
    description: spec.description,
    tier: "core",
    context: "session",
    version: null,
    sections: spec.sections,
    inputs: spec.inputs,
  }
}

export function move<T>(list: T[], index: number, delta: number): T[] {
  const target = index + delta
  if (target < 0 || target >= list.length) return list
  const next = [...list]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  return next
}

/** Server messages keyed by the dotted path into the spec they name (`sections.0.fields.1.label`). */
export type FieldErrors = Record<string, string[]>

/**
 * Turn a refused save or preview into messages by location. `strip` drops the
 * leading request parts (`body`, and `spec` for a preview) so both routes'
 * paths address the same spec. A refusal that names no location lands on `""`.
 */
export function fieldErrorsFrom(error: unknown, strip: string[] = ["body"]): FieldErrors {
  const issues = error instanceof ApiError ? (error.details?.validation as ValidationIssue[] | undefined) : undefined
  if (!issues) {
    const message = error instanceof Error && error.message ? error.message : "That couldn't be saved. Try again."
    return { "": [message] }
  }
  const errors: FieldErrors = {}
  for (const { loc, msg } of issues) {
    const parts = [...loc]
    while (parts.length && strip.includes(String(parts[0]))) parts.shift()
    const path = parts.join(".")
    ;(errors[path] ??= []).push(msg.replace(/^Value error, /, ""))
  }
  return errors
}

/** Messages for exactly these paths. */
export function errorsAt(errors: FieldErrors, ...paths: string[]): string[] {
  return paths.flatMap((p) => errors[p] ?? [])
}
