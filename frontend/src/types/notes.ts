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
 * Note-type registry key. The core distribution ships SOAP and Narrative;
 * additional types (DAP / BIRP / meeting / ...) may be registered by
 * downstream consumers. Treated as an open string at runtime; the
 * bundled keys are listed for static narrowing on the discriminated
 * `NoteContent` union.
 */
export type NoteType = "soap" | "narrative"

export type ExportStatus =
  | "not_queued"
  | "pending_review"
  | "approved"
  | "exported"
  | "skipped"

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
  content: Record<string, unknown> | null
  content_edited: Record<string, unknown> | null
  finalized_at: string | null
  quality_rating: number | null
  quality_rating_reason: string | null
  quality_rating_sections: string[] | null
  status: NoteGenerationStatus
  export_status: ExportStatus
  export_queued_at: string | null
  export_reviewed_at: string | null
  export_reviewed_by: string | null
  exported_at: string | null
  created_at: string
  updated_at: string
}

export interface PatientNotesListResponse {
  data: Note[]
  total: number
}

export interface UpdateNoteEditsRequest {
  content_edited: Record<string, unknown>
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
  note_type: NoteType | string
  content_edited?: Record<string, unknown> | null
  dictation_transcript?: TranscriptModel | null
}
