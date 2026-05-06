// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice API Functions
 *
 * API functions for per-practice administrative endpoints. The
 * `audio-retention` route is mounted by the SaaS overlay
 * (`/api/saas/practices/{practice_id}/audio-retention`) but from the
 * OSS frontend's perspective it's just a route on the same API origin.
 */

import { put } from "./client"

export const AUDIO_RETENTION_MIN_DAYS = 30
export const AUDIO_RETENTION_MAX_DAYS = 2555 // ~7 years
export const AUDIO_RETENTION_DEFAULT_DAYS = 365

export interface AudioRetentionResponse {
  practice_id: string
  audio_retention_days: number
}

/**
 * Update the per-practice audio retention window (days).
 *
 * @param practiceId - The practice id whose retention window is being set.
 * @param days - New retention window. Must be within [30, 2555]; the
 *   backend enforces this with a 422 response (and a DB CHECK).
 * @param token - Optional auth token for server-side calls.
 */
export async function updateAudioRetention(
  practiceId: string,
  days: number,
  token?: string,
): Promise<AudioRetentionResponse> {
  return put<AudioRetentionResponse>(
    `/api/saas/practices/${practiceId}/audio-retention`,
    { days },
    token,
  )
}
