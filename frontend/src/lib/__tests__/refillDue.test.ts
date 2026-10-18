// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { dueLabel } from "../refillDue"

const NY = "America/New_York"
// Monday 2026-10-12, 10:00 in New York.
const NOW = new Date("2026-10-12T14:00:00Z")
/** The end of a day in New York, the way the backend stamps due times (EDT, UTC-4). */
const endOf = (day: string) => `${day}T23:59:59-04:00`

describe("dueLabel", () => {
  it.each([
    [endOf("2026-10-12"), "Due today"],
    [endOf("2026-10-13"), "Due tomorrow"],
    [endOf("2026-10-15"), "Due Thursday"],
    [endOf("2026-10-24"), "Due Oct 24"],
  ])("names the day it is due (%s)", (dueAt, text) => {
    expect(dueLabel(dueAt, NOW, NY)).toEqual({ text, overdue: false })
  })

  it.each([
    [endOf("2026-10-11"), "Was due yesterday"],
    [endOf("2026-10-08"), "Was due Thursday"],
    [endOf("2026-10-01"), "Was due Oct 1"],
  ])("says when it was due once it has passed (%s)", (dueAt, text) => {
    expect(dueLabel(dueAt, NOW, NY)).toEqual({ text, overdue: true })
  })

  it("counts days in the clinician's zone, not UTC", () => {
    // 03:59 UTC on Friday is still Thursday evening in New York. Read in
    // UTC this would say Friday, a day later than the practice means.
    expect(dueLabel("2026-10-16T03:59:59Z", NOW, NY).text).toBe("Due Thursday")
  })
})
