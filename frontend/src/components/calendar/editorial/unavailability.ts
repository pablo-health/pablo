// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Attribution helpers for the day/week views: which rule blanks a whole day,
 * and which rules are in force on a date. Labels are only ever `summarize()`
 * of a matched rule. The shading itself is drawn from schedule.ts.
 */

import type { AvailabilityRule, RuleType } from "@/types/availability"
import { format } from "./dateUtils"

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
