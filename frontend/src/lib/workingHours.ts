// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AvailabilityRule } from "@/types/availability"

export interface WorkingHoursWindow {
  /** Hour (0-23) to scroll the calendar to: the earliest enabled start. */
  scrollToHour: number
  /** "HH:MM" earliest start across enabled days. */
  earliestStart: string
  /** "HH:MM" latest end across enabled days. */
  latestEnd: string
}

/**
 * The calendar's window comes from `working_hours` rules, not a separate
 * display-hours preference: min(start) to max(end) across enabled days, with
 * a scroll target of the earliest start. A day with no rule is disabled and
 * does not contribute. `null` when no day is enabled.
 */
export function deriveWorkingHoursWindow(rules: AvailabilityRule[]): WorkingHoursWindow | null {
  let earliestStart: string | null = null
  let latestEnd: string | null = null
  for (const rule of rules) {
    if (rule.rule_type !== "working_hours") continue
    const start = String(rule.params.start ?? "")
    const end = String(rule.params.end ?? "")
    if (start && (earliestStart === null || start < earliestStart)) earliestStart = start
    if (end && (latestEnd === null || end > latestEnd)) latestEnd = end
  }
  if (earliestStart === null || latestEnd === null) return null
  return {
    scrollToHour: Number(earliestStart.split(":")[0]),
    earliestStart,
    latestEnd,
  }
}

/** "09:30" -> "9:30 AM"; "17:00" -> "5 PM". */
export function formatClockTime(time: string): string {
  const [h, m] = time.split(":").map(Number)
  const period = h >= 12 ? "PM" : "AM"
  const hour12 = h % 12 === 0 ? 12 : h % 12
  return m ? `${hour12}:${String(m).padStart(2, "0")} ${period}` : `${hour12} ${period}`
}

/** Short zone abbreviation (e.g. "EST") for an IANA timezone name. */
export function timezoneAbbreviation(timezone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      timeZoneName: "short",
    }).formatToParts(new Date())
    return parts.find((p) => p.type === "timeZoneName")?.value ?? timezone
  } catch {
    return timezone
  }
}
