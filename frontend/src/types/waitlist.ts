// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The clinician's waitlist: clients waiting for a better or more regular time,
 * in the order the clinician would offer one.
 */

export type Weekday = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun"

export type DayPart = "morning" | "afternoon" | "evening"

export type WaitlistModality = "in_person" | "telehealth" | "either"

/** How a past visit was held. Never "either". */
export type VisitModality = Exclude<WaitlistModality, "either">

export const WEEKDAYS: readonly Weekday[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

export const DAY_PARTS: readonly DayPart[] = ["morning", "afternoon", "evening"]

export interface WaitlistEntry {
  id: string
  patient_id: string
  patient_name: string | null
  days: Weekday[]
  times: DayPart[]
  modality: WaitlistModality
  session_type: string | null
  /** 1 is the top. */
  position: number
  paused: boolean
  /** True when times are never offered for this entry without the clinician. */
  no_auto_offer: boolean
  created_at: string
  updated_at: string
}

export interface WaitlistResponse {
  /** Top first. */
  entries: WaitlistEntry[]
}

export interface PatientWaitlist {
  entry: WaitlistEntry | null
  /** The session type of the client's latest appointment, the default for a new entry. */
  usual_session_type: string | null
  /** How past visits were held: a suggestion to confirm, not a record. */
  past_modalities: VisitModality[]
}

export interface AddWaitlistEntryRequest {
  days: Weekday[]
  times: DayPart[]
  modality: WaitlistModality
  session_type?: string | null
  position?: number
  paused?: boolean
  no_auto_offer?: boolean
}

/** Only the fields sent change. */
export type UpdateWaitlistEntryRequest = Partial<
  Pick<WaitlistEntry, "days" | "times" | "modality" | "session_type" | "paused" | "no_auto_offer">
>

/** Server-side limit on a session type, mirrored for the input. */
export const WAITLIST_SESSION_TYPE_MAX = 30
