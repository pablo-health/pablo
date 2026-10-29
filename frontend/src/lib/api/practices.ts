// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice API Functions
 *
 * The caller's own practice's audio retention setting. Owner-only on the
 * backend: anyone else gets a 403.
 */

import { get, put } from "./client"

/** "Delete when the note is signed" rather than a number of days. */
export const AUDIO_RETENTION_ON_SIGNING = 0
export const AUDIO_RETENTION_MIN_DAYS = 1
export const AUDIO_RETENTION_MAX_DAYS = 2555 // ~7 years
export const AUDIO_RETENTION_DEFAULT_DAYS = 365

export interface AudioRetentionResponse {
  practice_id: string
  audio_retention_days: number
}

const AUDIO_RETENTION_PATH = "/api/users/me/practice/audio-retention"

/** The practice's audio retention setting. */
export async function getAudioRetention(token?: string): Promise<AudioRetentionResponse> {
  return get<AudioRetentionResponse>(AUDIO_RETENTION_PATH, token)
}

/**
 * Set the practice's audio retention.
 *
 * @param days - `AUDIO_RETENTION_ON_SIGNING` (0) to delete a session's audio
 *   once its note is signed, or a number of days within
 *   [AUDIO_RETENTION_MIN_DAYS, AUDIO_RETENTION_MAX_DAYS]. The backend enforces
 *   the range with a 422 response (and a DB CHECK).
 * @param token - Optional auth token for server-side calls.
 */
export async function updateAudioRetention(
  days: number,
  token?: string,
): Promise<AudioRetentionResponse> {
  return put<AudioRetentionResponse>(AUDIO_RETENTION_PATH, { audio_retention_days: days }, token)
}
