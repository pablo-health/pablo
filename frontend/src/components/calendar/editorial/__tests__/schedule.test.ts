// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import type { AvailabilityRule } from "@/types/availability"
import {
  nextWorkingDay,
  offHoursGaps,
  openingAnchor,
  pastUntilMinute,
  practiceDayRanges,
  workingRangesForDay,
  zonedInstant,
} from "../schedule"

const BROWSER_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

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

const MON_TO_THU_9_TO_5 = [0, 1, 2, 3].map((day) =>
  rule({ id: `wh-${day}`, params: { day_of_week: day, start: "09:00", end: "17:00" } }),
)

// October 2 2026 is a Friday; the week runs Sunday Sep 27 to Saturday Oct 3.
const FRIDAY_EVENING = new Date(2026, 9, 2, 19, 0)

describe("practiceDayRanges", () => {
  it("is the day's working hours, to the minute they end", () => {
    expect(practiceDayRanges(MON_TO_THU_9_TO_5, "2026-09-28")).toEqual([
      { startMin: 9 * 60, endMin: 17 * 60 },
    ])
  })

  it("is empty on a day with no hours", () => {
    expect(practiceDayRanges(MON_TO_THU_9_TO_5, "2026-10-02")).toEqual([])
  })

  it("cuts a blocked range out of the day", () => {
    const lunch = rule({ rule_type: "block_time_range", params: { start: "12:00", end: "13:00" } })
    expect(practiceDayRanges([...MON_TO_THU_9_TO_5, lunch], "2026-09-28")).toEqual([
      { startMin: 9 * 60, endMin: 12 * 60 },
      { startMin: 13 * 60, endMin: 17 * 60 },
    ])
  })

  it("empties a day a whole-day rule blocks", () => {
    const away = rule({
      rule_type: "block_specific_dates",
      params: { dates: ["2026-09-28"] },
    })
    expect(practiceDayRanges([...MON_TO_THU_9_TO_5, away], "2026-09-28")).toEqual([])
  })

  it("leaves buffers, caps and a type's own window to the booking surfaces", () => {
    const rules = [
      ...MON_TO_THU_9_TO_5,
      rule({ rule_type: "buffer_after", params: { minutes: 15 } }),
      rule({ rule_type: "max_per_day", params: { max: 1 } }),
      rule({
        appointment_type_id: "intake",
        params: { day_of_week: 0, start: "10:00", end: "11:00" },
      }),
    ]
    expect(practiceDayRanges(rules, "2026-09-28")).toEqual([{ startMin: 9 * 60, endMin: 17 * 60 }])
  })
})

describe("zonedInstant", () => {
  it("reads a wall-clock time in the practice's zone", () => {
    // 9 AM in New York in October is 13:00 UTC (EDT, UTC-4).
    expect(zonedInstant("2026-09-28", 9 * 60, "America/New_York")).toBe(
      Date.UTC(2026, 8, 28, 13, 0),
    )
    // And in January, 14:00 UTC (EST, UTC-5).
    expect(zonedInstant("2026-01-12", 9 * 60, "America/New_York")).toBe(
      Date.UTC(2026, 0, 12, 14, 0),
    )
  })
})

describe("workingRangesForDay", () => {
  it("places hours kept in another zone where they fall in this one", () => {
    // Monday 9 AM in Tokyo, wherever this test runs, is drawn starting at
    // that instant's local time.
    const instant = new Date(zonedInstant("2026-09-28", 9 * 60, "Asia/Tokyo"))
    const localDay = new Date(instant.getFullYear(), instant.getMonth(), instant.getDate())
    const ranges = workingRangesForDay(MON_TO_THU_9_TO_5, localDay, "Asia/Tokyo")
    expect(ranges.map((r) => r.startMin)).toContain(instant.getHours() * 60 + instant.getMinutes())

    // And across a whole week the four eight-hour days are all there, split
    // across local midnights however the zones fall.
    let total = 0
    for (let offset = -1; offset < 6; offset++) {
      const day = new Date(2026, 8, 27 + offset)
      for (const r of workingRangesForDay(MON_TO_THU_9_TO_5, day, "Asia/Tokyo")) {
        expect(r.startMin).toBeGreaterThanOrEqual(0)
        expect(r.endMin).toBeLessThanOrEqual(24 * 60)
        total += r.endMin - r.startMin
      }
    }
    expect(total).toBe(4 * 8 * 60)
  })

  it("is the stored hours as-is when the practice and the browser share a zone", () => {
    expect(workingRangesForDay(MON_TO_THU_9_TO_5, new Date(2026, 8, 28), BROWSER_ZONE)).toEqual([
      { startMin: 9 * 60, endMin: 17 * 60 },
    ])
  })
})

describe("offHoursGaps", () => {
  it("is the complement of working time within the drawn window", () => {
    expect(offHoursGaps([{ startMin: 9 * 60, endMin: 17 * 60 }], 7, 20)).toEqual([
      { startMin: 7 * 60, endMin: 9 * 60 },
      { startMin: 17 * 60, endMin: 20 * 60 },
    ])
  })

  it("is the whole window on a day with no hours", () => {
    expect(offHoursGaps([], 7, 20)).toEqual([{ startMin: 7 * 60, endMin: 20 * 60 }])
  })
})

describe("pastUntilMinute", () => {
  it("is all of an earlier day, up to now today, and none of a later day", () => {
    expect(pastUntilMinute(new Date(2026, 8, 28), FRIDAY_EVENING)).toBe(24 * 60)
    expect(pastUntilMinute(new Date(2026, 9, 2), FRIDAY_EVENING)).toBe(19 * 60)
    expect(pastUntilMinute(new Date(2026, 9, 3), FRIDAY_EVENING)).toBe(0)
  })
})

describe("nextWorkingDay", () => {
  it("skips a day off and the weekend to the next day with hours", () => {
    expect(nextWorkingDay(MON_TO_THU_9_TO_5, FRIDAY_EVENING, BROWSER_ZONE)).toEqual(
      new Date(2026, 9, 5),
    )
  })

  it("is today while today's hours are still ahead", () => {
    const mondayMorning = new Date(2026, 8, 28, 8, 0)
    expect(nextWorkingDay(MON_TO_THU_9_TO_5, mondayMorning, BROWSER_ZONE)).toEqual(
      new Date(2026, 8, 28),
    )
  })

  it("is null for a practice with no hours at all", () => {
    expect(nextWorkingDay([], FRIDAY_EVENING, BROWSER_ZONE)).toBeNull()
  })
})

describe("openingAnchor", () => {
  it("opens next week when nothing is left in this one", () => {
    expect(openingAnchor(MON_TO_THU_9_TO_5, [], FRIDAY_EVENING, BROWSER_ZONE)).toEqual(
      new Date(2026, 9, 5),
    )
  })

  it("stays on this week while working time is still ahead in it", () => {
    const wednesday = new Date(2026, 8, 30, 10, 0)
    expect(openingAnchor(MON_TO_THU_9_TO_5, [], wednesday, BROWSER_ZONE)).toBe(wednesday)
  })

  it("stays on this week while a session in it is still to come", () => {
    const saturdaySession = {
      start_at: new Date(2026, 9, 3, 10, 0).toISOString(),
      end_at: new Date(2026, 9, 3, 10, 50).toISOString(),
    }
    expect(
      openingAnchor(MON_TO_THU_9_TO_5, [saturdaySession], FRIDAY_EVENING, BROWSER_ZONE),
    ).toBe(FRIDAY_EVENING)
  })

  it("stays put for a practice with no hours", () => {
    expect(openingAnchor([], [], FRIDAY_EVENING, BROWSER_ZONE)).toBe(FRIDAY_EVENING)
  })
})
