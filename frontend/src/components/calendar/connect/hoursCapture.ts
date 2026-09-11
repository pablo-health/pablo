// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pure helpers for the calendar's first-run hours capture: the
 * plain-language echo a parse is confirmed through, and the timezone list
 * it is confirmed against. Kept out of the step itself so both can be read
 * (and tested) without rendering it.
 */

import {
  describeWorkingHours,
  selectionFromRules,
} from "@/components/availability/WorkingHoursGrid"
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
 * start/end collapse into one sentence ("Monday to Thursday, 09:00 to
 * 17:00"); every other rule keeps the parser's own summary. Grouping by
 * range rather than all together keeps the echo exact when a day has
 * different hours from the rest.
 */
export function echoLines(proposals: readonly ProposedAvailabilityRule[]): EchoLine[] {
  const byRange = new Map<string, number[]>()
  const others: EchoLine[] = []

  proposals.forEach((proposal, index) => {
    if (proposal.rule_type !== "working_hours") {
      others.push({ text: proposal.human_summary, indexes: [index] })
      return
    }
    const key = `${String(proposal.params.start)}-${String(proposal.params.end)}`
    const group = byRange.get(key)
    if (group) group.push(index)
    else byRange.set(key, [index])
  })

  const hours = [...byRange.values()].map((indexes) => {
    const selection = selectionFromRules(indexes.map((index) => proposals[index]))
    return {
      text: selection ? describeWorkingHours(selection) : proposals[indexes[0]].human_summary,
      indexes,
    }
  })

  return [...hours, ...others]
}
