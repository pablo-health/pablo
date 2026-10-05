// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client AI-notes consent API client:
 * `GET /api/patients/{patient_id}/ai-consent` — current answer and history,
 * `POST /api/patients/{patient_id}/ai-consent` — record an answer,
 * `GET`/`PUT /api/users/me/practice/ai-notes-consent` — whether the practice
 * asks clients at all.
 */

import type {
  AiConsentRecord,
  AiNotesConsentSetting,
  RecordAiConsentRequest,
} from "@/types/aiConsent"
import { get, post, put } from "./client"

export async function fetchAiConsent(
  patientId: string,
  token?: string,
): Promise<AiConsentRecord> {
  return get<AiConsentRecord>(`/api/patients/${patientId}/ai-consent`, token)
}

export async function recordAiConsent(
  patientId: string,
  data: RecordAiConsentRequest,
  token?: string,
): Promise<AiConsentRecord> {
  return post<AiConsentRecord>(`/api/patients/${patientId}/ai-consent`, data, token)
}

/** Readable by every clinician in the practice. */
export async function fetchAiNotesConsentSetting(token?: string): Promise<AiNotesConsentSetting> {
  return get<AiNotesConsentSetting>("/api/users/me/practice/ai-notes-consent", token)
}

/** The practice owner only; anyone else gets a 403. */
export async function updateAiNotesConsentSetting(
  askClientsAboutAiNotes: boolean,
  token?: string,
): Promise<AiNotesConsentSetting> {
  return put<AiNotesConsentSetting>(
    "/api/users/me/practice/ai-notes-consent",
    { ask_clients_about_ai_notes: askClientsAboutAiNotes },
    token,
  )
}
