// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session API Types
 *
 * TypeScript types matching backend Session API contracts.
 * Field names use snake_case to match backend exactly.
 */

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

/**
 * Export status enum
 *
 * Lifecycle for evaluation session exports:
 * 1. not_queued - Default, not selected for export
 * 2. pending_review - Queued, awaiting manual review
 * 3. approved - Reviewed and approved for export
 * 4. exported - Successfully exported to Braintrust
 * 5. skipped - Redaction failed or manually skipped
 */
export type ExportStatus =
  | "not_queued"
  | "pending_review"
  | "approved"
  | "exported"
  | "skipped"

/**
 * Transcript format enum
 */
export type TranscriptFormat = "vtt" | "json" | "txt"

/**
 * Transcript model containing format and content
 */
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
 * Full structured SOAP note with source references and derived narrative
 */
export interface StructuredSOAPNoteModel {
  subjective: SubjectiveNote
  objective: ObjectiveNote
  assessment: AssessmentNote
  plan: PlanNote
  narrative: SOAPNoteModel
}

/**
 * Session response from API
 *
 * Represents a therapy session with transcript, SOAP note, and metadata.
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
  // Flat narrative SOAP note (for PDF/clipboard backward compat)
  soap_note: SOAPNoteModel | null
  soap_note_edited: SOAPNoteModel | null
  // Structured SOAP note with source references
  soap_note_structured: StructuredSOAPNoteModel | null
  // Parsed transcript segments for source linking
  transcript_segments: TranscriptSegment[] | null
  quality_rating: number | null
  quality_rating_reason: string | null
  quality_rating_sections: string[] | null
  processing_started_at: string | null
  processing_completed_at: string | null
  finalized_at: string | null
  error: string | null
  // PII-redacted versions for review and export
  redacted_transcript: string | null
  naturalized_transcript: string | null
  redacted_soap_note: SOAPNoteModel | null
  naturalized_soap_note: SOAPNoteModel | null
  // Export queue tracking
  export_status: ExportStatus
  export_queued_at: string | null
  export_reviewed_at: string | null
  export_reviewed_by: string | null
  exported_at: string | null
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

/**
 * Request payload for uploading a session transcript
 */
export interface UploadSessionRequest {
  patient_id: string
  session_date: string
  transcript: TranscriptModel
}

/**
 * Request payload for finalizing a session after review
 */
export interface FinalizeSessionRequest {
  quality_rating: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
  soap_note_edited?: SOAPNoteModel
}

/**
 * Request payload for updating a session's quality rating
 */
export interface UpdateSessionRatingRequest {
  quality_rating: number
  quality_rating_reason?: string
  quality_rating_sections?: string[]
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
