// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pure geometry + attribution helpers for shading unavailable time in the
 * day/week views. Shading is the complement of the free-slots response
 * (geometry); labels are only ever `summarize()` of a matched rule, never a
 * re-derivation of the availability engine's own semantics — see the header
 * comment on each export below for exactly what it's allowed to know.
 */

import type { AvailabilityRule, RuleType, TimeSlot } from "@/types/availability"
import { format, minutesSinceMidnight } from "./dateUtils"

export interface UnavailableGap {
  startMin: number
  endMin: number
}

/** Rule types whose effect applies to every day rather than a specific
 * weekday or date — always "in force" once the rule exists. */
const DAY_INVARIANT_RULE_TYPES: RuleType[] = [
  "block_time_range",
  "max_per_day",
  "buffer_before",
  "buffer_after",
]

/**
 * date-fns `getDay()` returns Sunday=0..Saturday=6; a rule's `day_of_week`
 * param (checked backend-side against Python's `date.weekday()`) uses
 * Monday=0..Sunday=6 instead. Convert before comparing.
 */
export function jsWeekdayToRuleDay(jsDay: number): number {
  return (jsDay + 6) % 7
}

function dayOfWeekMatches(rule: AvailabilityRule, ruleDay: number): boolean {
  return Number(rule.params.day_of_week) === ruleDay
}

function dateRangeMatches(rule: AvailabilityRule, dateStr: string): boolean {
  const start = String(rule.params.start_date ?? "")
  const end = String(rule.params.end_date ?? "")
  return start !== "" && end !== "" && start <= dateStr && dateStr <= end
}

function specificDatesMatch(rule: AvailabilityRule, dateStr: string): boolean {
  const dates = Array.isArray(rule.params.dates) ? rule.params.dates.map(String) : []
  return dates.includes(dateStr)
}

/**
 * The rule that blanks this entire date, if any. Mirrors `_is_date_blocked`
 * in backend/app/scheduling_engine/services/availability.py exactly — only
 * the three rule types that can blank a whole day are considered. This is
 * the one case where a shaded band is attributed to a specific rule; the
 * caller labels the day with `summarize()` of the result.
 */
export function matchWholeDayBlockRule(
  rules: AvailabilityRule[],
  date: Date,
): AvailabilityRule | undefined {
  const dateStr = format(date, "yyyy-MM-dd")
  const ruleDay = jsWeekdayToRuleDay(date.getDay())
  return rules.find((rule) => {
    switch (rule.rule_type) {
      case "block_day_of_week":
        return dayOfWeekMatches(rule, ruleDay)
      case "block_date_range":
        return dateRangeMatches(rule, dateStr)
      case "block_specific_dates":
        return specificDatesMatch(rule, dateStr)
      default:
        return false
    }
  })
}

/**
 * Every rule that has some effect on this date — day-invariant rules, the
 * day-of-week rules that match, and the whole-day blockers that match.
 * Intra-day shading is never attributed to one rule out of this set; this
 * is only ever surfaced as an orienting list (a tooltip), not a per-band
 * attribution.
 */
export function rulesInForceForDate(
  rules: AvailabilityRule[],
  date: Date,
): AvailabilityRule[] {
  const dateStr = format(date, "yyyy-MM-dd")
  const ruleDay = jsWeekdayToRuleDay(date.getDay())
  return rules.filter((rule) => {
    if (DAY_INVARIANT_RULE_TYPES.includes(rule.rule_type)) return true
    if (rule.rule_type === "working_hours") return dayOfWeekMatches(rule, ruleDay)
    if (rule.rule_type === "block_day_of_week") return dayOfWeekMatches(rule, ruleDay)
    if (rule.rule_type === "block_date_range") return dateRangeMatches(rule, dateStr)
    if (rule.rule_type === "block_specific_dates") return specificDatesMatch(rule, dateStr)
    return false
  })
}

/**
 * The complement of the free slots within [dayStartHour, dayEndHour) —
 * the bands to shade as unavailable, in minutes since midnight. Pure
 * geometry against an assumed duration and the calendar as it stood a
 * moment ago; see the module doc in EditorialDayView.tsx/EditorialWeekView.tsx
 * for why this can only ever be guidance.
 */
export function computeUnavailableGaps(
  slots: TimeSlot[],
  dayStartHour: number,
  dayEndHour: number,
): UnavailableGap[] {
  const windowStart = dayStartHour * 60
  const windowEnd = dayEndHour * 60
  const free = slots
    .map((s) => ({
      start: Math.min(Math.max(minutesSinceMidnight(s.start), windowStart), windowEnd),
      end: Math.min(Math.max(minutesSinceMidnight(s.end), windowStart), windowEnd),
    }))
    .filter((s) => s.end > s.start)
    .sort((a, b) => a.start - b.start)

  const gaps: UnavailableGap[] = []
  let cursor = windowStart
  for (const f of free) {
    if (f.start > cursor) gaps.push({ startMin: cursor, endMin: f.start })
    cursor = Math.max(cursor, f.end)
  }
  if (cursor < windowEnd) gaps.push({ startMin: cursor, endMin: windowEnd })
  return gaps
}
