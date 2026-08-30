// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import type { BusyWindow, ProposedSeries } from "@/lib/api/scheduling"
import { busyCellKeys, cellKey, seriesCellKeys } from "../weekGrid"

function busy(startIso: string, endIso: string): BusyWindow {
  return { start: startIso, end: endIso }
}

function series(overrides: Partial<ProposedSeries> = {}): ProposedSeries {
  return {
    candidate_key: "key-1",
    summary: "Jane Miller",
    weekday: 0,
    local_start_time: "09:00",
    duration_minutes: 50,
    cadence: "weekly",
    occurrences_in_window: 8,
    occurrences_ahead: 4,
    first_future_start: "2026-09-07T09:00:00Z",
    last_seen: "2026-08-31T09:00:00Z",
    recurrence_rule: "RRULE:FREQ=WEEKLY",
    status: "active",
    confidence: 0.9,
    preselected: true,
    ...overrides,
  }
}

describe("busyCellKeys", () => {
  it("marks the hour a one-hour block falls in", () => {
    // Monday 2026-08-31, 09:00-10:00 local.
    const keys = busyCellKeys([busy("2026-08-31T09:00:00", "2026-08-31T10:00:00")])
    expect(keys).toEqual(new Set([cellKey(0, 9)]))
  })

  it("contributes one cell per hour a multi-hour block touches", () => {
    const keys = busyCellKeys([busy("2026-08-31T09:00:00", "2026-08-31T11:00:00")])
    expect(keys).toEqual(new Set([cellKey(0, 9), cellKey(0, 10)]))
  })

  it("drops a block outside the displayed weekday/hour range", () => {
    // Saturday, and an hour before the grid opens.
    const keys = busyCellKeys([
      busy("2026-09-05T09:00:00", "2026-09-05T10:00:00"),
      busy("2026-08-31T07:00:00", "2026-08-31T08:00:00"),
    ])
    expect(keys.size).toBe(0)
  })

  it("ignores a malformed or inverted window rather than throwing", () => {
    expect(() =>
      busyCellKeys([busy("not-a-date", "also-not-a-date"), busy("2026-08-31T10:00:00", "2026-08-31T09:00:00")])
    ).not.toThrow()
    expect(busyCellKeys([busy("not-a-date", "also-not-a-date")]).size).toBe(0)
  })
})

describe("seriesCellKeys", () => {
  it("maps a series to exactly the one cell its weekday and start hour name", () => {
    const keys = seriesCellKeys([series({ weekday: 2, local_start_time: "14:30" })])
    expect(keys).toEqual(new Set([cellKey(2, 14)]))
  })

  it("de-duplicates two series that land in the same cell", () => {
    const keys = seriesCellKeys([
      series({ candidate_key: "a", weekday: 0, local_start_time: "09:00" }),
      series({ candidate_key: "b", weekday: 0, local_start_time: "09:15" }),
    ])
    expect(keys).toEqual(new Set([cellKey(0, 9)]))
  })

  it("drops a series outside the displayed grid without throwing", () => {
    const keys = seriesCellKeys([series({ weekday: 5, local_start_time: "09:00" })])
    expect(keys.size).toBe(0)
  })
})
