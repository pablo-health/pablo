// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Reviewing a form that has been handed in.
 *
 * The clinician's half of the review cycle: reading a form back question by
 * question, asking for corrections on named questions, writing an answer
 * down for somebody in the room, and accepting the form.
 *
 * This is the clinician surface, so it rides the shared authenticated client
 * like every other chart read. The patient's half of the same form lives in
 * `patientIntake.ts`, which owns its own fetch because it carries a session
 * token instead of a signed-in user.
 *
 * The types live here rather than in `@/types` because nothing outside this
 * module and its hook has a use for them yet; move them out when a second
 * caller appears.
 */

import { get, getBlob, post } from "./client"

/** What a form can be doing, as the server reports it. */
export type IntakeAssignmentStatus =
  | "assigned"
  | "in_progress"
  | "submitted"
  | "needs_correction"
  | "accepted"
  | "withdrawn"

/** Where the answer on a question came from. Unset means nobody answered. */
export type IntakeAnswerProvenance = "patient" | "clinician"

/** One thing the practice — or the patient — did with this form. */
export type IntakeReviewEventKind =
  | "correction_requested"
  | "corrected"
  | "accepted"
  | "clinician_entered"

/**
 * How much of the form has an answer.
 *
 * `complete` is the server's own answer to "is this finished", and the only
 * thing a screen may say readiness from. `missing` names the questions it
 * counted.
 */
export interface IntakeProgress {
  complete: boolean
  missing: string[]
}

export interface IntakeAssignment {
  id: string
  version_id: string
  packet_name: string
  version: number
  status: IntakeAssignmentStatus
  assigned_at: string
  submitted_at: string | null
  receipt_code: string | null
  progress: IntakeProgress
}

/**
 * One question, what it currently holds, and where that came from.
 *
 * `superseded_count` is a count, not the answers themselves — there is no
 * route that reads the earlier answers back, so a screen can say how many
 * there were and nothing more.
 */
export interface IntakeReviewItem {
  id: string
  key: string
  position: number
  item_type: string
  required: boolean
  label: string | null
  help_text: string | null
  config: Record<string, unknown>
  value: Record<string, unknown> | null
  provenance: IntakeAnswerProvenance | null
  superseded_count: number
}

export interface IntakeReviewSignature {
  id: string
  assignment_id: string
  item_id: string
  document_version_id: string
  document_digest: string
  signer_role: string
  signer_typed_name: string
  consent_statement_version: string
  consent_statement: string
  signed_at: string
  auth_strength: string
  session_id: string | null
  evidence_digest: string
}

export interface IntakeReviewEvent {
  id: string
  kind: IntakeReviewEventKind
  item_ids: string[]
  note_to_patient: string | null
  created_by: string | null
  created_at: string
}

export interface IntakeReview extends IntakeAssignment {
  patient_id: string
  items: IntakeReviewItem[]
  signatures: IntakeReviewSignature[]
  events: IntakeReviewEvent[]
}

/**
 * One file a form collected, as the chart lists it.
 *
 * Carries what somebody needs in order to decide whether to open it — the
 * name, the kind, the size, and the question it answers in the practice's
 * own wording — and no bytes. Opening one goes through the clinician
 * document route the rest of the chart already uses.
 *
 * `scan_status` is `null` on a deployment with no scanner, which is every
 * deployment today. It is on the shape rather than missing from it because
 * "nobody has looked at this file" and "this file was found clean" are
 * different facts, and a screen must not read the first as the second.
 */
export interface IntakeChartArtifact {
  id: string
  item_id: string
  item_label: string
  side: string | null
  document_id: string
  filename: string
  content_type: string
  size_bytes: number
  scan_status: string | null
  created_at: string
}

/** The longest note the server will take on a correction request. */
export const MAX_CORRECTION_NOTE_LENGTH = 1000

function assignmentPath(patientId: string, assignmentId: string): string {
  return `/api/patients/${patientId}/intake-assignments/${assignmentId}`
}

/**
 * Which forms this patient was asked for, and how far each one has got.
 *
 * Not a disclosure and not audited: it carries which form was sent, when,
 * and a count of what is outstanding — no answer and no patient-authored
 * word. Reading what they answered is the review below.
 */
export async function listIntakeAssignments(
  patientId: string,
  token?: string,
): Promise<IntakeAssignment[]> {
  return get<IntakeAssignment[]>(`/api/patients/${patientId}/intake-assignments`, token)
}

/**
 * The files one form collected. Reading them is an audited disclosure.
 *
 * Its own call rather than a field on the review, because the chart shows
 * the files whenever it is open and the answers only when somebody asks
 * for them.
 */
export async function listIntakeArtifacts(
  patientId: string,
  assignmentId: string,
  token?: string,
): Promise<IntakeChartArtifact[]> {
  return get<IntakeChartArtifact[]>(
    `${assignmentPath(patientId, assignmentId)}/artifacts`,
    token,
  )
}

/** A form as the clinician reviews it. Reading it is an audited disclosure. */
export async function getIntakeReview(
  patientId: string,
  assignmentId: string,
  token?: string,
): Promise<IntakeReview> {
  return get<IntakeReview>(`${assignmentPath(patientId, assignmentId)}/review`, token)
}

/**
 * Reopen named questions and tell the patient why.
 *
 * The note is required by the server: a reopened question with nothing said
 * about it is the one thing the patient's screen exists to explain.
 */
export async function requestIntakeCorrection(
  patientId: string,
  assignmentId: string,
  body: { item_ids: string[]; note: string },
  token?: string,
): Promise<IntakeAssignment> {
  return post<IntakeAssignment>(
    `${assignmentPath(patientId, assignmentId)}/request-correction`,
    body,
    token,
  )
}

/** Done with the form. */
export async function acceptIntakeAssignment(
  patientId: string,
  assignmentId: string,
  token?: string,
): Promise<IntakeAssignment> {
  return post<IntakeAssignment>(
    `${assignmentPath(patientId, assignmentId)}/accept`,
    {},
    token,
  )
}

/**
 * The whole form as one file to keep, print or hand over.
 *
 * A blob rather than parsed content: the route sends a document, and the
 * only thing this side does with it is hand it to the browser to save.
 * Reading it is an audited disclosure, like the review beside it.
 */
export async function downloadIntakeExport(
  patientId: string,
  assignmentId: string,
  token?: string,
): Promise<Blob> {
  return getBlob(`${assignmentPath(patientId, assignmentId)}/export`, token)
}

/**
 * What the file is saved as, matching the name the route sends.
 *
 * The receipt is what a practice and a patient can both quote. A form that
 * has not been handed in has no receipt, so it is named after the request
 * it answers instead — the same fallback the route makes.
 */
export function intakeExportFilename(assignmentId: string, receiptCode: string | null): string {
  return `intake-${receiptCode || assignmentId}.html`
}

/** Write an answer down for somebody sitting in the room. */
export async function enterIntakeAnswerForPatient(
  patientId: string,
  assignmentId: string,
  itemId: string,
  value: Record<string, unknown>,
  token?: string,
): Promise<IntakeAssignment> {
  return post<IntakeAssignment>(
    `${assignmentPath(patientId, assignmentId)}/items/${itemId}/clinician-entry`,
    { value },
    token,
  )
}
