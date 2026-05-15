// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  updateAudioRetention,
  type AudioRetentionResponse,
} from "@/lib/api/practices"
import { useAuthMutation } from "./useAuthQuery"

interface UpdateAudioRetentionVariables {
  practiceId: string
  days: number
}

/**
 * Mutation hook for updating per-practice audio retention.
 *
 * Wraps the per-practice audio-retention PUT endpoint. The backend
 * response is the canonical persisted value; the parent component is
 * responsible for surfacing success/error UI.
 */
export function useAudioRetention(token?: string) {
  return useAuthMutation<
    AudioRetentionResponse,
    UpdateAudioRetentionVariables
  >({
    mutationFn: ({ practiceId, days }) =>
      updateAudioRetention(practiceId, days, token),
  })
}
