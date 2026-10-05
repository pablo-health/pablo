// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client's answer about AI-assisted notes (recording, transcription and
 * drafting). Mirrors backend `app.models.client_ai_consent`.
 *
 * The answer applies to every session until the client gives a different
 * one. `current` is the latest answer, or `null` when nobody has asked yet;
 * `history` is every answer, oldest first.
 */

export type AiConsentDecision = "consented" | "declined"

export type AiConsentSource = "clinician" | "intake_form"

export interface AiConsentEntry {
  id: string
  decision: AiConsentDecision
  /** The day the client gave the answer, as YYYY-MM-DD. */
  effective_on: string
  source: AiConsentSource
  recorded_by_name: string | null
  recorded_at: string
}

export interface AiConsentRecord {
  current: AiConsentEntry | null
  history: AiConsentEntry[]
}

export interface RecordAiConsentRequest {
  decision: AiConsentDecision
  /** Defaults to today on the server when omitted. */
  effective_on?: string
}
