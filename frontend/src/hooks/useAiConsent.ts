// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { AiConsentRecord, RecordAiConsentRequest } from "@/types/aiConsent"
import { fetchAiConsent, recordAiConsent } from "@/lib/api/aiConsent"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The client's current answer about AI-assisted notes, and its history. */
export function useAiConsent(patientId: string | undefined, token?: string) {
  return useAuthQuery<AiConsentRecord>({
    queryKey: queryKeys.aiConsent.byPatient(patientId ?? ""),
    queryFn: () => fetchAiConsent(patientId!, token),
    enabled: !!patientId,
  })
}

export function useRecordAiConsent(token?: string) {
  return useAuthMutation<
    AiConsentRecord,
    { patientId: string; data: RecordAiConsentRequest }
  >({
    mutationFn: ({ patientId, data }) => recordAiConsent(patientId, data, token),
    invalidateKeys: ({ patientId }) => [queryKeys.aiConsent.byPatient(patientId)],
  })
}
