// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A waitlist entry and a client's usual times share one shape: days, times of
 * day and format. A new entry starts from the client's active
 * `scheduling.availability`; an entry that ends up different can be saved
 * back to it, but only when the clinician asks.
 */

import {
  AVAILABILITY,
  scheduleWindow,
  type Preference,
  type ScheduleWindow,
} from "@/types/patientPreferences"
import { DAY_PARTS, WEEKDAYS } from "@/types/waitlist"

/** The client's one active availability row, if they have one. */
export function activeAvailability(preferences: Preference[]): Preference | null {
  return preferences.find((p) => p.kind === AVAILABILITY && p.retired_at === null) ?? null
}

/** The availability row's window, in the waitlist's day and time order. */
export function usualWindow(availability: Preference | null): ScheduleWindow | null {
  const window = availability ? scheduleWindow(availability) : null
  if (!window) return null
  return {
    days: WEEKDAYS.filter((day) => window.days.includes(day)),
    times: DAY_PARTS.filter((part) => window.times.includes(part)),
    modality: window.modality,
  }
}

function sameMembers<T>(a: readonly T[], b: readonly T[]): boolean {
  return a.length === b.length && a.every((v) => b.includes(v))
}

export function sameWindow(a: ScheduleWindow, b: ScheduleWindow | null): boolean {
  return (
    b !== null &&
    a.modality === b.modality &&
    sameMembers(a.days, b.days) &&
    sameMembers(a.times, b.times)
  )
}
