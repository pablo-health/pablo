// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note-Type catalog types
 *
 * Mirrors the backend `NoteTypeRegistry` shape exposed at
 * `GET /api/note-types`. The registry is the source of truth for which
 * note types the user can pick when starting a session.
 */

import type { TranscriptModel } from "./sessions"

export type NoteFieldKind = "text" | "list" | "diagnoses" | "structured"

export type NoteTier = "core" | "extension"

/**
 * Lifecycle context for a note type.
 * - `session`: bound to one session record (SOAP, Narrative, DAP, BIRP, GIRP)
 * - `patient`: bound to a patient, versioned (safety plan, intake, treatment plan)
 * - `practice`: bound to clinic-level workflows (supervision, audits)
 */
export type NoteContext = "session" | "patient" | "practice"

export interface NoteFieldSchema {
  key: string
  label: string
  kind: NoteFieldKind
  ai_hint: string
}

export interface NoteSectionSchema {
  key: string
  label: string
  fields: NoteFieldSchema[]
  /**
   * Drafted for review beside the note (the evidence for the medical
   * decision making), never shown or printed as part of the note.
   */
  review_only?: boolean
}

export type NoteInputKind = "text" | "choice"

/**
 * A value supplied when a note of this type is generated — set on the
 * appointment. `options` only applies to `choice`.
 */
export interface NoteInputSchema {
  key: string
  label: string
  kind: NoteInputKind
  options: string[]
  required: boolean
  /** The value a note takes when none was chosen. */
  default?: string | null
}

export interface NoteTypeSchema {
  key: string
  label: string
  description: string
  tier: NoteTier
  context: NoteContext
  sections: NoteSectionSchema[]
  inputs: NoteInputSchema[]
  /** Version of a practice-defined type; null for built-in types. */
  version: number | null
  /**
   * True for a note only its author can read (a psychotherapy note). Such
   * a note is written by hand and is never generated from a transcript.
   */
  restricted?: boolean
  /**
   * True when the caller's subscription / role does not permit creating
   * a note of this type. Defaults to false for OSS (allow-all
   * authorizer); SaaS sets true for Practice-tier extension types
   * (DAP, BIRP, GIRP, ...) until the caller upgrades. Pickers should
   * render locked types with an upgrade affordance rather than as live
   * options.
   */
  is_locked?: boolean
  /**
   * The stored definition of a practice-defined type, prompts included.
   * Only the single-type read returns it; null for built-in types.
   */
  spec?: PracticeNoteTypeSpec | null
  /** For a practice type built on a base: the base and how much it changes. */
  based_on?: BasedOn | null
}

export interface NoteTypeListResponse {
  note_types: NoteTypeSchema[]
}

/** Key prefix of a practice's own note types (`custom.<slug>`). */
export const PRACTICE_KEY_PREFIX = "custom."

export function isPracticeKey(key: string): boolean {
  return key.startsWith(PRACTICE_KEY_PREFIX)
}

/** The field shapes a practice can give its own types. */
export type PracticeFieldKind = "text" | "list" | "diagnoses"

export interface PracticeFieldSpec {
  key: string
  label: string
  kind: PracticeFieldKind
  ai_hint: string
}

export interface PracticeSectionSpec {
  key: string
  label: string
  fields: PracticeFieldSpec[]
  review_only?: boolean
}

export interface PracticeInputSpec {
  key: string
  label: string
  kind: NoteInputKind
  options: string[]
  required: boolean
  default?: string | null
}

/**
 * The body `PUT /api/note-types/custom/{slug}` takes: a practice's own note type.
 *
 * Either full (its own sections, inputs and prompts) or based on a built-in:
 * then `base` and `patch` are set, and the sections, inputs and prompts are
 * empty because the server takes them from the base each time it reads the type.
 */
export interface PracticeNoteTypeSpec {
  label: string
  description: string
  system_prompt: string
  user_template: string | null
  sections: PracticeSectionSpec[]
  inputs: PracticeInputSpec[]
  base?: string
  patch?: NoteTypePatch
}

/** What a based type changes about its base. Paths are `section` or `section.field`. */
export interface NoteTypePatch {
  add_sections: { section: PracticeSectionSpec; after: string | null }[]
  add_fields: { section: string; field: PracticeFieldSpec; after: string | null }[]
  hide_fields: string[]
  hide_sections: string[]
  override: { path: string; label?: string | null; ai_hint?: string | null }[]
  add_inputs: PracticeInputSpec[]
  system_prompt_append: string | null
}

/** A practice type's base, and how much it changes. */
export interface BasedOn {
  key: string
  label: string
  additions: number
  hidden: number
}

/** A synthetic visit transcript to try a draft on. */
export interface SampleVisit {
  id: string
  label: string
  transcript: string
}

/** A built-in a practice can base its own type on (`GET /api/note-types/bases`). */
export interface NoteTypeBase {
  key: string
  label: string
  description: string
  /** The slug a type based on this one saves under, when it is free. */
  slug: string
  spec: PracticeNoteTypeSpec
  /** Fields (`section.field`) a based type cannot hide. */
  required_fields: string[]
  samples: SampleVisit[]
}

export interface NoteTypeBaseListResponse {
  bases: NoteTypeBase[]
}

export interface NoteDraftPreviewRequest {
  key?: string
  version?: number
  spec?: PracticeNoteTypeSpec
  transcript: TranscriptModel
  inputs?: Record<string, string>
}

/** A draft shaped like a generated note's content. Nothing is saved. */
export interface NoteDraftPreviewResponse {
  key: string
  version: number | null
  sections: Record<string, Record<string, unknown>>
}

/** What `POST /api/note-types/derive` proposes from: send at least one sample or a description. */
export interface DeriveNoteTypeRequest {
  /** Pasted note text, one per sample. */
  samples: string[]
  /** PDF, Word or text files, one note each. */
  files: File[]
  description: string
  /** A note type or reference key to compare the proposal against. */
  reference: string | null
}

/** One sample's check against the proposal. */
export interface DeriveCoverage {
  /** Index of the sample: pasted samples first, then files. */
  sample: number
  passages: number
  /** Passages no proposed field took, verbatim. */
  unplaced: string[]
  /** How many lines were left out as not note content (signatures, header facts); absent from older servers. */
  excluded?: number
  /** False when the sample could not be checked; `unplaced` is then empty. */
  checked: boolean
}

export interface DeriveGuardFinding {
  path: string
  outcome: string
}

export interface NoteTypeReference {
  key: string
  label: string
}

export interface DeriveSuggestion {
  label: string
  description: string
}

/** A proposed note type and the checks run on it. Nothing is saved. */
export interface DeriveNoteTypeResponse {
  spec: PracticeNoteTypeSpec
  coverage: DeriveCoverage[]
  guard: DeriveGuardFinding[]
  reference: NoteTypeReference | null
  /** Elements of the reference the proposal has no section or field for. */
  suggestions: DeriveSuggestion[]
}

export interface NoteTypeReferenceListResponse {
  references: NoteTypeReference[]
}

export const DEFAULT_NOTE_TYPE = "soap"
