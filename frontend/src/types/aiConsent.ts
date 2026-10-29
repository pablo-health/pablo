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

/** Where the answer was given. */
export type AiConsentModality = "in_person" | "telehealth"

/** Who gave the answer: the client, or a parent or guardian for them. */
export type AiConsentGiver = "client" | "parent" | "guardian"

export interface AiConsentEntry {
  id: string
  decision: AiConsentDecision
  /** The day the client gave the answer, as YYYY-MM-DD. */
  effective_on: string
  source: AiConsentSource
  recorded_by_name: string | null
  recorded_at: string
  /** How the answer was given; `null` (or absent) when nobody said. */
  modality?: AiConsentModality | null
  /** Where the client said they were, in their words (telehealth). */
  client_stated_location?: string | null
  consented_by?: AiConsentGiver | null
}

export interface AiConsentRecord {
  current: AiConsentEntry | null
  history: AiConsentEntry[]
}

/**
 * Whether the practice asks clients to agree to AI-assisted notes. Mirrors
 * backend `app.routes.practice_ai_notes_consent.AiNotesConsentSetting`.
 */
export interface AiNotesConsentSetting {
  ask_clients_about_ai_notes: boolean
  /** How long the practice keeps session audio. Read aloud in the script. */
  audio_retention_days: number
  /** Whether the caller may change the setting (the practice owner). */
  can_change: boolean
}

export interface RecordAiConsentRequest {
  decision: AiConsentDecision
  /** Defaults to today on the server when omitted. */
  effective_on?: string
  modality?: AiConsentModality
  client_stated_location?: string
  consented_by?: AiConsentGiver
}
