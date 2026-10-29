// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Due Thursday": a refill request's due time as the practice reads it.
 *
 * The due time is the end of the last working day of the practice's own
 * target (see `backend/app/services/refill_turnaround.py`), so it is read
 * as a day, never a clock time. Days are counted in the clinician's zone:
 * a request due at the end of Thursday in New York is due Thursday there,
 * whatever the date is in UTC.
 *
 * Within the coming week the weekday name is enough and reads the way
 * people say it; further out, or further back, a date says which Thursday.
 */

export interface DueLabel {
  text: string
  overdue: boolean
}

const WEEKDAY: Intl.DateTimeFormatOptions = { weekday: "long" }
const SHORT_DATE: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" }
const DAY_MS = 24 * 60 * 60 * 1000

/** The calendar day an instant falls on in `timeZone`, as a UTC-midnight timestamp. */
function localDay(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(instant)
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return Date.UTC(part("year"), part("month") - 1, part("day"))
}

function dayName(instant: Date, days: number, timeZone: string): string {
  const options = Math.abs(days) <= 6 ? WEEKDAY : SHORT_DATE
  return instant.toLocaleDateString("en-US", { ...options, timeZone })
}

export function dueLabel(dueAt: string, now: Date, timeZone: string): DueLabel {
  const due = new Date(dueAt)
  const days = Math.round((localDay(due, timeZone) - localDay(now, timeZone)) / DAY_MS)
  if (due.getTime() < now.getTime()) {
    const when = days === -1 ? "yesterday" : dayName(due, days, timeZone)
    return { text: `Was due ${when}`, overdue: true }
  }
  if (days === 0) return { text: "Due today", overdue: false }
  if (days === 1) return { text: "Due tomorrow", overdue: false }
  return { text: `Due ${dayName(due, days, timeZone)}`, overdue: false }
}
