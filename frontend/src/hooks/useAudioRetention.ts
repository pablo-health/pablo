// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getAudioRetention,
  updateAudioRetention,
  type AudioRetentionResponse,
} from "@/lib/api/practices"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The practice's audio retention setting (practice owner only). */
export function useAudioRetentionSetting(token?: string) {
  return useAuthQuery<AudioRetentionResponse>({
    queryKey: queryKeys.audioRetention.all,
    queryFn: () => getAudioRetention(token),
  })
}

interface UpdateAudioRetentionVariables {
  days: number
}

/**
 * Mutation hook for updating the practice's audio retention.
 *
 * The backend response is the canonical persisted value; the parent component
 * is responsible for surfacing success/error UI. The read-aloud consent script
 * reads the same setting, so its query is refreshed too.
 */
export function useAudioRetention(token?: string) {
  return useAuthMutation<AudioRetentionResponse, UpdateAudioRetentionVariables>({
    mutationFn: ({ days }) => updateAudioRetention(days, token),
    invalidateKeys: [queryKeys.audioRetention.all, queryKeys.aiConsent.practiceSetting()],
  })
}
