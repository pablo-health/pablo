// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AiConsentRecord,
  AiNotesConsentSetting,
  RecordAiConsentRequest,
} from "@/types/aiConsent"
import {
  fetchAiConsent,
  fetchAiNotesConsentSetting,
  recordAiConsent,
  updateAiNotesConsentSetting,
} from "@/lib/api/aiConsent"
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

/** Whether the practice asks clients about AI-assisted notes, and its audio retention window. */
export function useAiNotesConsentSetting(token?: string) {
  return useAuthQuery<AiNotesConsentSetting>({
    queryKey: queryKeys.aiConsent.practiceSetting(),
    queryFn: () => fetchAiNotesConsentSetting(token),
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Whether to ask, for the script and the note line. False until the setting
 * has loaded: showing either before then would claim a setting nobody read.
 */
export function useAsksClientsAboutAiNotes(): boolean {
  const { data } = useAiNotesConsentSetting()
  return data?.ask_clients_about_ai_notes === true
}

export function useUpdateAiNotesConsentSetting(token?: string) {
  return useAuthMutation<AiNotesConsentSetting, boolean>({
    mutationFn: (ask) => updateAiNotesConsentSetting(ask, token),
    onSuccess: (data, _ask, queryClient) => {
      queryClient.setQueryData(queryKeys.aiConsent.practiceSetting(), data)
    },
  })
}
