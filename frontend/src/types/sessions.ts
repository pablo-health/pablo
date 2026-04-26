// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session API Types
 *
 * Sessions are recording-only artifacts (audio, transcript, status). The
 * durable clinical Note lives on a separate ``notes`` table and is embedded
 * here as ``note`` for the read paths the frontend already uses. See
 * ``./notes.ts`` for the Note shape.
 */

import type { Note, NoteType } from "./notes"

export type { Note, NoteType, ExportStatus } from "./notes"

/**
 * Session status enum
 *
 * Lifecycle:
 * 1. queued - Created, waiting for processing
 * 2. processing - SOAP generation in progress
 * 3. pending_review - SOAP generated, waiting for therapist review
 * 4. finalized - Therapist has reviewed and approved
 * 5. failed - SOAP generation failed
 */
export type SessionStatus =
  | "scheduled"
  | "in_progress"
  | "recording_complete"
  | "cancelled"
  | "queued"
  | "processing"
  | "pending_review"
  | "finalized"
  | "failed"

export type TranscriptFormat = "vtt" | "json" | "txt"

export interface TranscriptModel {
  format: TranscriptFormat
  content: string
}

/**
 * SOAP Note model (narrative strings for display/PDF/clipboard)
 *
 * SOAP = Subjective, Objective, Assessment, Plan
 * Standard clinical documentation format for therapy sessions.
 */
export interface SOAPNoteModel {
  subjective: string
  objective: string
  assessment: string
  plan: string
}

/**
 * Polymorphic note content discriminated by `note_type`. The Note's
 * ``content`` / ``content_edited`` JSONB columns deserialize into one of
 * these shapes per the note-type registry.
 */
export type NoteContent = SOAPNoteContent | NarrativeNoteContent

export interface SOAPNoteContent extends SOAPNoteModel {
  note_type: "soap"
}

export interface NarrativeNoteContent {
  note_type: "narrative"
  body: string
}

/**
 * A single AI-generated claim with transcript provenance.
 * source_segment_ids links to TranscriptSegment indices.
 */
export interface SOAPSentence {
  text: string
  source_segment_ids: number[]
  confidence_score: number
  confidence_level: string // "high" | "medium" | "low" | "unverified"
  possible_match_segment_ids: number[]
  signal_used: string
}

/**
 * Structured sub-fields for Subjective section (with source references)
 */
export interface SubjectiveNote {
  chief_complaint: SOAPSentence
  mood_affect: SOAPSentence
  symptoms: SOAPSentence[] | null
  client_narrative: SOAPSentence
}

/**
 * Structured sub-fields for Objective section (with source references)
 */
export interface ObjectiveNote {
  appearance: SOAPSentence
  behavior: SOAPSentence
  speech: SOAPSentence
  thought_process: SOAPSentence
  affect_observed: SOAPSentence
}

/**
 * Structured sub-fields for Assessment section (with source references)
 */
export interface AssessmentNote {
  clinical_impression: SOAPSentence
  progress: SOAPSentence
  risk_assessment: SOAPSentence
  functioning_level: SOAPSentence
}

/**
 * Structured sub-fields for Plan section (with source references)
 */
export interface PlanNote {
  interventions_used: SOAPSentence[] | null
  homework_assignments: SOAPSentence[] | null
  next_steps: SOAPSentence[] | null
  next_session: SOAPSentence
}

/**
 * A single parsed transcript segment for source linking.
 */
export interface TranscriptSegment {
  index: number
  speaker: string
  text: string
  start_time: number
  end_time: number
}

/**
 * Full structured SOAP note with source references and derived narrative.
 *
 * Persisted under ``Note.content`` (and ``Note.content_edited`` when the
 * clinician edits in-place). The frontend extracts the structured tree via
 * the helpers in ``@/lib/utils/notes``.
 */
export interface StructuredSOAPNoteModel {
  subjective: SubjectiveNote
  objective: ObjectiveNote
  assessment: AssessmentNote
  plan: PlanNote
  narrative: SOAPNoteModel
}

/**
 * Session response from API.
 *
 * Recording-only metadata (audio, transcript, status). Note content (SOAP
 * body, edits, quality, export status) lives under ``note`` — frontend
 * should read ``response.note.*`` rather than legacy flat fields, which no
 * longer exist on the backend after pa-0nx.2.
 */
export interface SessionResponse {
  id: string
  user_id: string
  patient_id: string
  patient_name: string
  session_date: string
  session_number: number
  status: SessionStatus
  transcript: TranscriptModel
  created_at: string
  // Companion scheduling fields
  scheduled_at: string | null
  video_link: string | null
  video_platform: string | null
  session_type: string | null
  duration_minutes: number | null
  source: string | null
  notes: string | null
  started_at: string | null
  ended_at: string | null
  updated_at: string | null
  transcript_segments: TranscriptSegment[] | null
  processing_started_at: string | null
  processing_completed_at: string | null
  error: string | null
  // PII-redacted transcript variants
  redacted_transcript: string | null
  naturalized_transcript: string | null
  // Embedded note (None when this session has no generated note yet).
  note: Note | null
}

/**
 * Paginated list of sessions
 */
export interface SessionListResponse {
  data: SessionResponse[]
  total: number
  page: number
  page_size: number
}

export interface UploadSessionRequest {
  patient_id: string
  session_date: string
  transcript: TranscriptModel
}

export interface FinalizeSessionRequest {
  quality_rating: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
  soap_note_edited?: SOAPNoteModel
}

export interface UpdateSessionRatingRequest {
  quality_rating: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
}

/**
 * Export queue item for admin review
 */
export interface ExportQueueItem {
  id: string
  user_id: string
  patient_name: string
  session_date: string
  session_number: number
  quality_rating: number | null
  redacted_transcript: string | null
  redacted_soap_note: SOAPNoteModel | null
  export_status: import("./notes").ExportStatus
  export_queued_at: string | null
  finalized_at: string | null
}

export interface ExportQueueListResponse {
  data: ExportQueueItem[]
  total: number
}

export interface ExportActionRequest {
  action: "approve" | "skip" | "flag"
  reason?: string
}

/**
 * Clinician observation data for the Objective section.
 * Captures details the AI cannot infer from transcript alone.
 */
export interface ClinicalObservation {
  appearance: string
  eye_contact: string
  psychomotor: string
  psychomotor_notes: string
  attitude: string
  non_verbal: string
  affect_observation: string
}

// --- Helpers ----------------------------------------------------------------

/**
 * Project a Note's polymorphic JSONB content into the discriminated
 * ``NoteContent`` union the editor renders against. Returns ``null`` if the
 * note has no content yet (e.g. pre-generation).
 */
export function noteContentFromNote(
  note: Pick<Note, "note_type" | "content"> | null,
): NoteContent | null {
  return note ? projectContent(note.note_type, note.content) : null
}

/**
 * Same projection for a Note's ``content_edited`` field.
 */
export function noteEditedContentFromNote(
  note: Pick<Note, "note_type" | "content_edited"> | null,
): NoteContent | null {
  return note ? projectContent(note.note_type, note.content_edited) : null
}

/**
 * Pull the ``StructuredSOAPNoteModel`` (with source references) out of a
 * SOAP note's ``content`` JSONB if present.
 */
export function structuredSoapFromNote(
  note: Pick<Note, "note_type" | "content"> | null,
): StructuredSOAPNoteModel | null {
  if (!note || note.note_type !== "soap" || !note.content) return null
  const c = note.content as Record<string, unknown>
  if (
    typeof c.subjective === "object" &&
    c.subjective !== null &&
    "chief_complaint" in (c.subjective as Record<string, unknown>)
  ) {
    return note.content as unknown as StructuredSOAPNoteModel
  }
  return null
}

function projectContent(
  noteType: NoteType,
  raw: Record<string, unknown> | null | undefined,
): NoteContent | null {
  if (!raw) return null
  if (noteType === "soap") {
    const narrative = (raw.narrative ?? raw) as Record<string, unknown>
    return {
      note_type: "soap",
      subjective: (narrative.subjective as string | undefined) ?? "",
      objective: (narrative.objective as string | undefined) ?? "",
      assessment: (narrative.assessment as string | undefined) ?? "",
      plan: (narrative.plan as string | undefined) ?? "",
    }
  }
  return {
    note_type: "narrative",
    body: (raw.body as string | undefined) ?? "",
  }
}

/**
 * Re-shape a discriminated ``NoteContent`` value back into the JSONB shape
 * the backend expects under ``content_edited``.
 */
export function noteContentToJson(
  content: NoteContent,
): Record<string, unknown> {
  if (content.note_type === "soap") {
    const { note_type: _t, ...rest } = content
    void _t
    return { ...rest }
  }
  const { note_type: _t, ...rest } = content
  void _t
  return { ...rest }
}
