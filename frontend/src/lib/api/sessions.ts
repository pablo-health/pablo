// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session API Functions
 *
 * Type-safe wrappers for session management API endpoints.
 */

import type {
  FinalizeSessionRequest,
  SessionListResponse,
  SessionResponse,
  UpdateSessionRatingRequest,
  UploadSessionRequest,
} from "@/types/sessions"
import { get, patch, post } from "./client"

/**
 * Upload a session transcript for SOAP note generation
 *
 * This creates a new session, generates a SOAP note using AI,
 * and returns the session in "pending_review" status.
 *
 * Flow:
 * 1. Creates session with status="queued"
 * 2. Generates SOAP note (synchronous in MVP)
 * 3. Updates to status="pending_review"
 * 4. Updates patient session_count and last_session_date
 *
 * @param patientId - Patient ID for this session
 * @param data - Session data including transcript
 * @param token - Optional auth token for server-side calls
 * @returns Created session with AI-generated SOAP note
 * @throws ApiError with code "NOT_FOUND" if patient doesn't exist
 * @throws ApiError with code "SOAP_GENERATION_FAILED" if AI generation fails
 *
 * @example
 * const session = await uploadSession("patient-123", {
 *   patient_id: "patient-123",
 *   session_date: "2024-01-15T14:30:00Z",
 *   transcript: {
 *     format: "vtt",
 *     content: "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHow have you been feeling?"
 *   }
 * })
 * // session.status === "pending_review"
 * // session.soap_note !== null
 */
export async function uploadSession(
  patientId: string,
  data: UploadSessionRequest,
  token?: string
): Promise<SessionResponse> {
  return post<SessionResponse>(
    `/api/patients/${patientId}/sessions/upload`,
    data,
    token
  )
}

/**
 * List all sessions for the current user
 *
 * @param token - Optional auth token for server-side calls
 * @returns Paginated list of sessions across all patients
 *
 * @example
 * const sessions = await listSessions()
 * // Returns all sessions ordered by session_date (newest first)
 */
export async function listSessions(token?: string): Promise<SessionListResponse> {
  return get<SessionListResponse>("/api/sessions", token)
}

/**
 * Get a single session by ID
 *
 * @param sessionId - Session ID
 * @param token - Optional auth token for server-side calls
 * @returns Session details including transcript and SOAP note
 * @throws ApiError with code "NOT_FOUND" if session doesn't exist
 *
 * @example
 * const session = await getSession("session-123")
 * console.log(session.patient_name) // "Doe, Jane"
 * console.log(session.soap_note?.subjective)
 */
export async function getSession(
  sessionId: string,
  token?: string
): Promise<SessionResponse> {
  return get<SessionResponse>(`/api/sessions/${sessionId}`, token)
}

/**
 * Finalize a session after therapist review
 *
 * Moves session from "pending_review" to "finalized" status.
 * Therapist provides quality rating and optionally edits the SOAP note.
 *
 * @param sessionId - Session ID
 * @param data - Quality rating and optional edited SOAP note
 * @param token - Optional auth token for server-side calls
 * @returns Updated session with status="finalized"
 * @throws ApiError with code "INVALID_STATUS" if session is not in pending_review
 *
 * @example
 * // Finalize without editing SOAP
 * const finalized = await finalizeSession("session-123", {
 *   quality_rating: 5
 * })
 *
 * // Finalize with edited SOAP
 * const edited = await finalizeSession("session-123", {
 *   quality_rating: 4,
 *   soap_note_edited: {
 *     subjective: "Patient reports improved mood...",
 *     objective: "Patient appeared calm and engaged...",
 *     assessment: "Continued progress with CBT techniques...",
 *     plan: "Continue weekly sessions, review coping strategies"
 *   }
 * })
 * // edited.soap_note_edited !== null
 */
export async function finalizeSession(
  sessionId: string,
  data: FinalizeSessionRequest,
  token?: string
): Promise<SessionResponse> {
  return patch<SessionResponse>(`/api/sessions/${sessionId}/finalize`, data, token)
}

/**
 * Update quality rating for an already-finalized session
 *
 * Allows therapist to update their quality rating after finalization.
 *
 * @param sessionId - Session ID
 * @param data - New quality rating (1-5)
 * @param token - Optional auth token for server-side calls
 * @returns Updated session
 * @throws ApiError with code "INVALID_STATUS" if session is not finalized
 *
 * @example
 * const updated = await updateSessionRating("session-123", {
 *   quality_rating: 5
 * })
 */
export async function updateSessionRating(
  sessionId: string,
  data: UpdateSessionRatingRequest,
  token?: string
): Promise<SessionResponse> {
  return patch<SessionResponse>(`/api/sessions/${sessionId}/rating`, data, token)
}
