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
 * Note-type registry key. OSS ships SOAP and Narrative; SaaS adds DAP / BIRP /
 * meeting on top. Treated as an open string at runtime; the OSS-known keys
 * are listed for static narrowing on the discriminated `NoteContent` union.
 */
export type NoteType = "soap" | "narrative"

export type ExportStatus =
  | "not_queued"
  | "pending_review"
  | "approved"
  | "exported"
  | "skipped"

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
  quality_rating: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
}

export interface CreateStandaloneNoteRequest {
  note_type: NoteType | string
  content_edited?: Record<string, unknown> | null
  dictation_transcript?: TranscriptModel | null
}
