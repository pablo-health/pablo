// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient preferences: how to work with a client, on the client's record.
 * Scheduling kinds carry a structured value in the waitlist's vocabulary;
 * note kinds carry the clinician's words.
 */

import type { DayPart, WaitlistModality, Weekday } from "@/types/waitlist"

export const AVAILABILITY = "scheduling.availability"
export const HARD_LIMIT = "scheduling.hard_limit"
export const COMMUNICATION_NOTE = "communication.note"
export const PRACTICAL_NOTE = "practical.note"

export type CoreKind =
  | typeof AVAILABILITY
  | typeof HARD_LIMIT
  | typeof COMMUNICATION_NOTE
  | typeof PRACTICAL_NOTE

export const CORE_KINDS: readonly CoreKind[] = [
  AVAILABILITY,
  HARD_LIMIT,
  COMMUNICATION_NOTE,
  PRACTICAL_NOTE,
]

/** Kinds a client has at most one active row of. */
export const SINGLE_ACTIVE_KINDS: readonly CoreKind[] = [
  AVAILABILITY,
  COMMUNICATION_NOTE,
  PRACTICAL_NOTE,
]

export type Strength = "hard" | "soft"

export type PreferenceSource = "clinician" | "assistant" | "learned" | "intake" | "migrated"

export interface ScheduleWindow {
  days: Weekday[]
  times: DayPart[]
  modality: WaitlistModality
}

export interface Preference {
  id: string
  patient_id: string
  domain: string
  kind: string
  kind_label: string
  value: Record<string, unknown> | null
  text: string | null
  strength: Strength | null
  /** Plain words for the value, when the kind supplies them. */
  summary: string | null
  source: PreferenceSource
  stated_at: string
  confirmed_at: string | null
  retired_at: string | null
  created_at: string
  updated_at: string
}

export interface PreferenceListResponse {
  data: Preference[]
  total: number
}

export interface AddPreferenceRequest {
  kind: string
  value?: ScheduleWindow | null
  text?: string | null
  strength?: Strength | null
}

/** Only the fields sent change. */
export type UpdatePreferenceRequest = Partial<Pick<AddPreferenceRequest, "value" | "text" | "strength">>

/** Server-side limit on a preference's words, mirrored for the input. */
export const PREFERENCE_TEXT_MAX = 280

export function isCoreKind(kind: string): kind is CoreKind {
  return (CORE_KINDS as readonly string[]).includes(kind)
}

export function scheduleWindow(preference: Preference): ScheduleWindow | null {
  const value = preference.value
  if (!value || !Array.isArray(value.days) || !Array.isArray(value.times)) return null
  return value as unknown as ScheduleWindow // the server validated the shape against the kind
}
