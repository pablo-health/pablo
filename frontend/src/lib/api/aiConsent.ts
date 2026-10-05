// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client AI-notes consent API client:
 * `GET /api/patients/{patient_id}/ai-consent` — current answer and history,
 * `POST /api/patients/{patient_id}/ai-consent` — record an answer.
 */

import type { AiConsentRecord, RecordAiConsentRequest } from "@/types/aiConsent"
import { get, post } from "./client"

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
