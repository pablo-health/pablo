// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note API types
 *
 * Mirrors backend `app.models.notes.NoteResponse`. Notes are the durable
 * clinical artifact (SOAP, narrative, ...) — owned by a patient, optionally
 * tied to a recorded session.
 */

import type { TranscriptModel } from "./sessions"

/**
 * Note-type registry key. SOAP and Narrative have their own editors; every
 * other built-in type (DAP, BIRP, ...) and a practice's own types
 * (`custom.<slug>`) render from their catalog definition. `string & {}`
 * keeps the key open while leaving the two bespoke literals visible to
 * autocomplete.
 */
export type NoteType = "soap" | "narrative" | (string & {})

/**
 * Lifecycle of the standalone-note dictation path: 'processing' from the
 * moment the skeleton is persisted, until the Cloud Tasks worker writes
 * 'complete' (with content) or 'failed'. Every note created any other way
 * (no dictation, session-derived) is 'complete' from creation.
 */
export type NoteGenerationStatus = "processing" | "complete" | "failed"

/**
 * Patient-owned clinical note. Mirrors `NoteResponse` from the backend.
 */
export interface Note {
  id: string
  patient_id: string
  session_id: string | null
  note_type: NoteType
  /** Version of a practice-defined type the note was written against; null for built-in types. */
  note_type_version: number | null
  /** Values supplied for the note type's declared inputs. */
  note_inputs: Record<string, string> | null
  content: Record<string, unknown> | null
  content_edited: Record<string, unknown> | null
  finalized_at: string | null
  quality_rating: number | null
  quality_rating_reason: string | null
  quality_rating_sections: string[] | null
  status: NoteGenerationStatus
  /** Who wrote the note; null on rows that predate the column. */
  author_user_id: string | null
  /** Readable by its author alone (a psychotherapy note). */
  restricted: boolean
  /** A reference copy of a note signed where it was written; never signed here. */
  imported?: boolean
  /** Where an archive import brought the note from; null for a note written here. */
  source?: NoteSource | null
  created_at: string
  updated_at: string
}

/** A diagnosis code an imported note carried, as its export listed it. */
export interface NoteDiagnosisCode {
  code: string
  description: string
}

/** Where an imported note came from, as its document and export state it. */
export interface NoteSource {
  /** The system it was exported from, as the importer names it ("simplepractice"). */
  system: string
  /** The note's class there ("progress", "chart", "treatment_plan"...). */
  kind: string | null
  /** The note's title there, as the export printed it. */
  title: string | null
  /** Who signed it there, as the document writes it; never a user here. */
  signed_by: string | null
  signed_at: string | null
  created_at: string | null
  /** The visit the note belongs to, when it has one. */
  visit_date: string | null
  diagnoses: NoteDiagnosisCode[]
}

export interface PatientNotesListResponse {
  data: Note[]
  total: number
}

export interface UpdateNoteEditsRequest {
  content_edited: Record<string, unknown>
}

/**
 * What a redraft does with the clinician's edits: keep every field they
 * changed and redraft the rest, or replace the note with the new draft.
 */
export type RedraftEdits = "keep" | "replace"

/** Mirrors `RedraftNoteRequest` (`POST /api/sessions/{id}/note/redraft`). */
export interface RedraftNoteRequest {
  /** Replaces the note's inputs before drafting. */
  note_inputs?: Record<string, string>
  /** Required once the note has edits; the API answers 409 NOTE_HAS_EDITS without it. */
  edits?: RedraftEdits
}

export interface FinalizeNoteRequest {
  /**
   * Optional — required for AI-generated session notes (clinician rates
   * the model's draft) and omitted for manually-authored notes (nothing
   * to score). Backend treats absent ↔ null.
   */
  quality_rating?: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
}

export interface CreateStandaloneNoteRequest {
  note_type: NoteType
  /** Values for the note type's declared inputs. */
  note_inputs?: Record<string, string> | null
  content_edited?: Record<string, unknown> | null
  dictation_transcript?: TranscriptModel | null
}

/** A signature as entered for one signing: prefilled from the profile, editable. */
export interface NoteSignerFields {
  signer_name: string
  signer_credentials?: string | null
}

export interface SignNoteRequest extends NoteSignerFields {
  quality_rating?: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
}

export interface UnlockNoteRequest {
  reason: string
}

export interface CreateNoteAddendumRequest extends NoteSignerFields {
  text: string
  /** The dictation the addendum was drafted from, when it was. */
  dictation_id?: string
}

/** One signed version of a note. Mirrors `NoteSignatureResponse`. */
export interface NoteSignature {
  id: string
  version: number
  signed_by: string
  signer_name: string
  signer_credentials: string | null
  signed_at: string
  /** Set once the version was unlocked to correct an error. */
  unlocked_at: string | null
  unlocked_by: string | null
  unlock_reason: string | null
  note_type: NoteType
  note_type_version: number | null
  /** The body as it was signed. */
  content: Record<string, unknown> | null
  content_edited: Record<string, unknown> | null
}

/** Information added to a signed note, with its own signature. */
export interface NoteAddendum {
  id: string
  text: string
  signer_name: string
  signer_credentials: string | null
  created_by: string
  created_at: string
}

/** Mirrors `NoteSigningRecordResponse` — what a signature block shows. */
export interface NoteSigningRecord {
  note_id: string
  finalized_at: string | null
  /** The version the note stands on now; null when unsigned, unlocked or finalized before signatures. */
  signature: NoteSignature | null
  /** Every signed version, oldest first. */
  versions: NoteSignature[]
  addenda: NoteAddendum[]
}
