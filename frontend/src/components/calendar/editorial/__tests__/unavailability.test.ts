// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import type { AvailabilityRule } from "@/types/availability"
import {
  computeUnavailableGaps,
  jsWeekdayToRuleDay,
  matchWholeDayBlockRule,
  rulesInForceForDate,
} from "../unavailability"

// June 5 2026 is a Friday; June 1 2026 is the preceding Monday.
const FRIDAY = new Date(2026, 5, 5)
const MONDAY = new Date(2026, 5, 1)

function rule(overrides: Partial<AvailabilityRule> = {}): AvailabilityRule {
  return {
    id: "r1",
    user_id: "u1",
    rule_type: "working_hours",
    enforcement: "hard",
    params: {},
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

describe("computeUnavailableGaps", () => {
  it("shades only the hours outside a partial day's free slots", () => {
    const slots = [{ start: "2026-06-05T09:00:00", end: "2026-06-05T12:00:00" }]
    expect(computeUnavailableGaps(slots, 7, 20)).toEqual([
      { startMin: 7 * 60, endMin: 9 * 60 },
      { startMin: 12 * 60, endMin: 20 * 60 },
    ])
  })

  it("shades nothing when free slots cover the whole window", () => {
    const slots = [{ start: "2026-06-05T07:00:00", end: "2026-06-05T20:00:00" }]
    expect(computeUnavailableGaps(slots, 7, 20)).toEqual([])
  })

  it("shades the entire window when there are no free slots (a blocked day)", () => {
    expect(computeUnavailableGaps([], 7, 20)).toEqual([{ startMin: 7 * 60, endMin: 20 * 60 }])
  })

  it("merges multiple free slots into the remaining gaps, in order", () => {
    const slots = [
      { start: "2026-06-05T14:00:00", end: "2026-06-05T15:00:00" },
      { start: "2026-06-05T09:00:00", end: "2026-06-05T10:00:00" },
    ]
    expect(computeUnavailableGaps(slots, 7, 20)).toEqual([
      { startMin: 7 * 60, endMin: 9 * 60 },
      { startMin: 10 * 60, endMin: 14 * 60 },
      { startMin: 15 * 60, endMin: 20 * 60 },
    ])
  })
})

describe("jsWeekdayToRuleDay", () => {
  it("maps date-fns/JS weekday numbering onto the rule's Monday=0 numbering", () => {
    expect(jsWeekdayToRuleDay(0)).toBe(6) // JS Sunday -> rule Sunday
    expect(jsWeekdayToRuleDay(1)).toBe(0) // JS Monday -> rule Monday
    expect(jsWeekdayToRuleDay(5)).toBe(4) // JS Friday -> rule Friday
  })
})

describe("matchWholeDayBlockRule", () => {
  it("matches a block_day_of_week rule on the blocked weekday", () => {
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })
    expect(matchWholeDayBlockRule([blockFriday], FRIDAY)).toBe(blockFriday)
    expect(matchWholeDayBlockRule([blockFriday], MONDAY)).toBeUndefined()
  })

  it("matches a block_date_range rule covering the date", () => {
    const blocked = rule({
      rule_type: "block_date_range",
      params: { start_date: "2026-06-03", end_date: "2026-06-06" },
    })
    expect(matchWholeDayBlockRule([blocked], FRIDAY)).toBe(blocked)
    expect(matchWholeDayBlockRule([blocked], MONDAY)).toBeUndefined()
  })

  it("matches a block_specific_dates rule listing the date", () => {
    const blocked = rule({
      rule_type: "block_specific_dates",
      params: { dates: ["2026-06-05"] },
    })
    expect(matchWholeDayBlockRule([blocked], FRIDAY)).toBe(blocked)
    expect(matchWholeDayBlockRule([blocked], MONDAY)).toBeUndefined()
  })

  it("never matches on rule types that don't blank a whole day", () => {
    const workingHours = rule({
      rule_type: "working_hours",
      params: { day_of_week: 4, start: "09:00", end: "17:00" },
    })
    expect(matchWholeDayBlockRule([workingHours], FRIDAY)).toBeUndefined()
  })
})

describe("rulesInForceForDate", () => {
  it("includes day-invariant rules regardless of the date", () => {
    const buffer = rule({ rule_type: "buffer_after", params: { minutes: 15 } })
    expect(rulesInForceForDate([buffer], MONDAY)).toEqual([buffer])
    expect(rulesInForceForDate([buffer], FRIDAY)).toEqual([buffer])
  })

  it("includes working_hours only on its matching weekday", () => {
    const fridayHours = rule({
      rule_type: "working_hours",
      params: { day_of_week: 4, start: "09:00", end: "17:00" },
    })
    expect(rulesInForceForDate([fridayHours], FRIDAY)).toEqual([fridayHours])
    expect(rulesInForceForDate([fridayHours], MONDAY)).toEqual([])
  })
})
