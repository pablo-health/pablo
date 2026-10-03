// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pure helpers for the calendar's first-run hours capture: the
 * plain-language echo a parse is confirmed through, and the timezone list
 * it is confirmed against. Kept out of the step itself so both can be read
 * (and tested) without rendering it.
 */

import { describeDays } from "@/components/availability/WorkingHoursGrid"
import { formatClockTime } from "@/lib/workingHours"
import type { ProposedAvailabilityRule } from "@/types/availability"

/** A short, common list for the deployments (and test runtimes) where the
 * browser cannot enumerate zones itself. */
const FALLBACK_TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "Europe/London",
  "UTC",
]

export function timezoneOptions(current: string): string[] {
  const supported =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : []
  const zones = supported.length > 0 ? [...supported] : [...FALLBACK_TIMEZONES]
  return zones.includes(current) ? zones : [current, ...zones]
}

/** One confirmable line of the echo, and the proposals it stands for. */
export interface EchoLine {
  text: string
  indexes: number[]
}

/**
 * Plain-language lines for a parse. Working-hours proposals sharing a
 * start/end collapse into one sentence ("Monday to Thursday, 9:00 AM to
 * 5:00 PM"); every other rule keeps the parser's own summary. Grouping by
 * range rather than all together keeps the echo exact when a day has
 * different hours from the rest.
 */
export function echoLines(proposals: readonly ProposedAvailabilityRule[]): EchoLine[] {
  const byRange = new Map<string, number[]>()
  const others: EchoLine[] = []

  proposals.forEach((proposal, index) => {
    // A window scoped to one appointment type is a different rule from the
    // practice's general hours at the same times, and the grid's wording
    // knows nothing of types: it stands on its own line, in the parser's
    // words, which name the type. Grouping it would let one Remove drop the
    // general hours too.
    if (proposal.rule_type !== "working_hours" || proposal.appointment_type_id) {
      others.push({ text: proposal.human_summary, indexes: [index] })
      return
    }
    const key = `${String(proposal.params.start)}-${String(proposal.params.end)}`
    const group = byRange.get(key)
    if (group) group.push(index)
    else byRange.set(key, [index])
  })

  // The range is read from the proposals themselves rather than rounded to
  // the grid's whole hours, so "9:30 to 5" echoes as 9:30, not 9:00.
  const hours = [...byRange.values()].map((indexes) => {
    const group = indexes.map((index) => proposals[index])
    const days = group.map((proposal) => proposal.params.day_of_week)
    const { start, end } = group[0].params
    const readable =
      days.every((day): day is number => typeof day === "number") &&
      typeof start === "string" &&
      typeof end === "string"
    return {
      text: readable
        ? `${describeDays(new Set(days as number[]))}, ${formatClockTime(start)} to ${formatClockTime(end)}`
        : group[0].human_summary,
      indexes,
    }
  })

  return [...hours, ...others]
}
